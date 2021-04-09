import os
import io
import csv
import shutil
import logging
from glob import glob
from pprint import pprint

import git
import requests
from requests.auth import HTTPBasicAuth

logging.basicConfig(level=logging.DEBUG)

class ProgressPrinter(git.RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=""):
        print(op_code, cur_count, max_count, cur_count / (max_count or 100.0), message or "NO MESSAGE")

class URLListManager:
    def __init__(self, working_dir, push_repo, master_repo, github_token, ssh_key_path):
        self.working_dir = working_dir
        self.push_repo = push_repo
        self.github_user = push_repo.split("/")[0]
        self.github_token = github_token

        self.master_repo = master_repo
        self.ssh_key_path = ssh_key_path
        self.repo_dir = os.path.join(self.working_dir, "test-lists")

        self.repo = self.init_repo()

    def init_repo(self):
        logging.debug("initializing repo")
        if not os.path.exists(self.repo_dir):
            logging.debug("cloning repo")
            repo = git.Repo.clone_from(
                    f"git@github.com:{self.master_repo}.git",
                    self.repo_dir,
                    branch="master"
            )
            repo.create_remote("rworigin", f"git@github.com:{self.push_repo}.git")
        repo = git.Repo(self.repo_dir)
        repo.remotes.origin.pull(progress=ProgressPrinter())
        return repo

    def get_git_env(self):
        return self.repo.git.custom_environment(GIT_SSH_COMMAND=f"ssh -i {self.ssh_key_path}")

    def get_user_repo_path(self, username):
        return os.path.join(self.working_dir, "users", username, "test-lists")

    def get_user_statefile_path(self, username):
        return os.path.join(self.working_dir, "users", username, "state")

    def get_user_pr_path(self, username):
        return os.path.join(self.working_dir, "users", username, "pr_id")

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
                return in_file.read()
        except FileNotFoundError:
            return "CLEAN"

    def set_state(self, username, state):
        """
        This will record the current state of the pull request for the user to the statefile.

        The absence of a statefile is an indication of a clean state.
        """
        assert state in ("DIRTY", "PUSHING", "PR_OPEN", "CLEAN")

        logging.debug(f"setting state for {username} to {state}")
        if state == "CLEAN":
            os.remove(self.get_user_statefile_path(username))
            os.remove(self.get_user_pr_path(username))
            return

        with open(self.get_user_statefile_path(username), "w") as out_file:
            out_file.write(state)

    def set_pr_id(self, username, pr_id):
        with open(self.get_user_pr_path(username), "w") as out_file:
            out_file.write(pr_id)

    def get_pr_id(self, username):
        with open(self.get_user_pr_path(username)) as in_file:
            return in_file.read()

    def get_user_repo(self, username):
        repo_path = self.get_user_repo_path(username)
        if not os.path.exists(repo_path):
            print(f"creating {repo_path}")
            self.repo.git.worktree("add", "-b", self.get_user_branchname(username), repo_path)
        return git.Repo(repo_path)

    def get_test_list(self, username):
        self.sync_state(username)

        repo_path = self.get_user_repo_path(username)
        if not os.path.exists(repo_path):
            repo_path = self.repo_dir

        test_lists = {}
        for path in glob(os.path.join("lists", "*.csv")):
            cc = os.path.basename(path).split(".")[0]
            if not len(cc) == 2 and not cc == "global":
                continue
            with open(path) as tl_file:
                csv_reader = csv.reader(tl_file)
                for line in csv_reader:
                    test_lists[cc] = test_lists.get(cc, [])
                    test_lists[cc].append(line)
        return test_lists

    def sync_state(self, username):
        state = self.get_state(username)
        # If the state is CLEAN or DIRTY we don't have to do anything
        if state == "CLEAN":
            return
        if state == "DIRTY":
            return
        if state == "PR_OPEN":
            if self.is_pr_resolved(username):
                shutil.rmtree(self.get_user_repo_path(username))
                self.repo.git.worktree("prune")

                self.set_state(username, "CLEAN")

    def add(self, username, cc, new_entry, comment):
        self.sync_state(username)

        logging.debug("adding new entry")

        state = self.get_state(username)
        if state in ("PUSHING", "PR_OPEN"):
            raise Exception("You cannot edit files while changes are pending")

        repo = self.get_user_repo(username)
        filepath = os.path.join(self.get_user_repo_path(username), "lists", f"{cc}.csv")

        with open(filepath, "a") as out_file:
            csv_writer = csv.writer(out_file, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            csv_writer.writerow(new_entry)
        repo.index.add([filepath])
        repo.index.commit(comment)

        self.set_state(username, "DIRTY")

    def edit(self, username, cc, old_entry, new_entry, comment):
        self.sync_state(username)

        logging.debug("editing existing entry")

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
        head = f"{self.github_user}:{branchname}"
        logging.debug(f"opening a PR for {head}")

        r = requests.post(
            f"https://api.github.com/repos/{self.master_repo}/pulls",
            auth=HTTPBasicAuth(self.github_user, self.github_token),
            json={
                "head": head,
                "base": "master",
                "title": "Pull requests from the web",
            }
        )
        j = r.json()
        logging.debug(j)
        return j["url"]

    def is_pr_resolved(self, username):
        r = requests.post(
            self.get_pr_id(),
            auth=HTTPBasicAuth(self.github_user, self.github_token),
        )
        j = r.json()
        return j["state"] != "open"

    def push_to_repo(self, username):
        with self.get_git_env():
            self.repo.remotes.rworigin.push(
                    self.get_user_branchname(username),
                    progress=ProgressPrinter(),
                    force=True
            )

    def propose_changes(self, username):
        self.set_state(username, "PUSHING")

        logging.debug("proposing changes")

        self.push_to_repo(username)

        pr_id = self.open_pr(self.get_user_branchname(username))
        self.set_pr_id(username, pr_id)
        self.set_state(username, "PR_OPEN")

def main():
    with open("GITHUB_TOKEN") as in_file:
        github_token = in_file.read().strip()

    ulm = URLListManager(
        working_dir=os.path.abspath("working_dir"),
        ssh_key_path=os.path.expanduser("~/.ssh/id_rsa_ooni-bot"),
        master_repo="hellais/test-lists",
        push_repo="ooni-bot/test-lists",
        github_token=github_token
    )

    #test_lists = tlm.get_test_list("antani")
    #pprint(test_lists)
    ulm.add("antani", "it", [
        "https://apple.com/",
        "FILE",
        "File-sharing",
        "2017-04-12",
        "",
        ""
    ], "add apple.com to italian test list")
    ulm.edit("antani", "it", [
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
    ulm.propose_changes("antani")

if __name__ == "__main__":
    main()
