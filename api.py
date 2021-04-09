import os
import io
import csv
import shutil

from pprint import pprint

import git
from gitdb import IStream
from git.index.typ import BaseIndexEntry

WORKING_DIR = os.path.abspath("working_dir")
REPO_DIR = os.path.join(WORKING_DIR, "test-lists")
MASTER_REPO_URL = "git@github.com:citizenlab/test-lists.git"
PUSH_REPO_URL = "git@github.com:hellais/test-lists.git"

class ProgressPrinter(git.RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=""):
        print(op_code, cur_count, max_count, cur_count / (max_count or 100.0), message or "NO MESSAGE")

def clone_repo():
    return git.Repo.clone_from(MASTER_REPO_URL, REPO_DIR, branch="master")

def init_repo():
    if not os.path.exists(REPO_DIR):
        repo = clone_repo()
        repo.create_remote("rworigin", PUSH_REPO_URL)
    repo = git.Repo(REPO_DIR)
    repo.remotes.origin.pull(progress=ProgressPrinter())
    return repo

class TestListManager:
    def __init__(self):
        self.repo = init_repo()

    def get_user_repo_path(self, username):
        return os.path.join(WORKING_DIR, "users", username, "test-lists")

    def get_user_statefile_path(self, username):
        return os.path.join(WORKING_DIR, "users", username, "state")

    def get_user_pr_path(self, username):
        return os.path.join(WORKING_DIR, "users", username, "pr_id")

    def get_user_branchname(self, username):
        return f"user-contribution/{username}"

    def get_state(self, username):
        """
        Returns the current state of the repo for the given user.

        The possible states are:
        - CLEAN:
            when we are in sync with the current tip of master and no changes have been made
        - DIRTY:
            when there are some changes in the working tree of the user, but they haven't yet pushed them
        - PUSHING:
            when we are pushing the changes made by the user via propose_changes
        - PR_OPEN:
            when the PR of the user is open on github and it's waiting for being merged
        """
        try:
            with open(self.get_user_statefile_path(username), "r") as in_file:
                return out_file.read()
        except FileNotFoundError:
            return "CLEAN"

    def set_state(self, username, state):
        """
        This will record the current state of the pull request for the user to the statefile.

        The absence of a statefile is an indication of a clean state.
        """
        assert state in ("DIRTY", "PUSHING", "PR_OPEN")
        with open(self.get_user_statefile_path(username), "w") as out_file:
            out_file.write(state)

    def set_pr_id(self, username, pr_id):
        with open(self.get_user_pr_path(username), "w") as out_file:
            out_file.write(pr_id)

    def get_user_repo(self, username):
        repo_path = self.get_user_repo_path(username)
        if not os.path.exists(repo_path):
            print(f"creating {repo_path}")
            self.repo.git.worktree("add", "-b", self.get_user_branchname(username), repo_path)
        return git.Repo(repo_path)

    def get_test_list(self, username):
        repo_path = self.get_user_repo_path(username)
        if not os.path.exists(repo_path):
            repo_path = REPO_DIR

        test_lists = {}
        for path in glob(os.path.join("lists", "*.csv")):
            cc = os.path.basename(path).split(".")[0]
            if not len(cc) == 2 and not cc == "global":
                continue
            with open(path) as tl_file:
                csv_reader = csv.DictReader(tl_file)
                for line in csv_reader:
                    test_lists[cc] = test_lists.get(cc, [])
                    test_lists[cc].append(dict(line))
        return test_lists

    def add(self, username, cc, new_entry, comment):
        repo = self.get_user_repo(username)
        if state in ("PUSHING", "PR_OPEN"):
            raise Exception("You cannot edit files while changes are pending")

        filepath = os.path.join(self.get_user_repo_path(username), "lists", f"{cc}.csv")

        with open(filepath, "a") as out_file:
            csv_writer = csv.writer(out_file, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            csv_writer.writerow(new_entry)
        repo.index.add([filepath])
        repo.index.commit(comment)

        self.set_state(username, "DIRTY")

    def edit(self, username, cc, old_entry, new_entry, comment):
        state = self.get_state(username)
        if state in ("PUSHING", "PR_OPEN"):
            raise Exception("You cannot edit the files while changes are pending")

        repo = self.get_user_repo(username)

        filepath = os.path.join(self.get_user_repo_path(username), "lists", f"{cc}.csv")

        out_buffer = io.StringIO()
        with open(filepath, "r") as in_file:
            csv_reader = csv.reader(in_file)
            csv_writer = csv.writer(out_buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")

            found = False
            for row in csv_reader:
                if row == old_entry:
                    found = True
                    csv_writer.writerow(new_entry)
                else:
                    csv_writer.writerow(row)
        if not found:
            raise Exception("Could not find the specified row")

        with open(filepath, "w") as out_file:
            out_buffer.seek(0)
            shutil.copyfileobj(out_buffer, out_file)
        repo.index.add([filepath])
        repo.index.commit(comment)

        self.set_state(username, "DIRTY")

    def open_pr(self, branchname):
        return "12345"

    def is_pr_resolved(self, username):
        return False

    def propose_changes(self, username):
        self.set_state(username, "PUSHING")

        shutil.rmtree(self.get_user_repo_path(username))
        self.repo.git.worktree("prune")
        self.repo.remotes.rworigin.push(self.get_user_branchname(username), progress=ProgressPrinter(), force=True)

        pr_id = self.open_pr(self.get_user_branchname(username))
        self.set_pr_id(username, pr_id)
        self.set_state(username, "PR_OPEN")

def main():
    tlm = TestListManager()
    #test_lists = tlm.get_test_list("antani")
    #pprint(test_lists)
    tlm.add("antani", "it", [
        "https://apple.com/",
        "FILE",
        "File-sharing",
        "2017-04-12",
        ""
        ""
    ], "add apple.com to italian test list")
    tlm.edit("antani", "it", [
        "http://btdigg.org/",
        "FILE",
        "File-sharing",
        "2017-04-12",
        "",
        "Site reported to be blocked by AGCOM - Italian Autority on Communication"
    ], [
        "https://btdigg.org/",
        "FILE",
        "File-sharing",
        "2017-04-12",
        "",
        "Site reported to be blocked by AGCOM - Italian Autority on Communication"
    ], "add https to the website url")
    tlm.propose_changes("antani")

if __name__ == "__main__":
    main()
