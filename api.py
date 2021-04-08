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
REPO_URL = "git@github.com:citizenlab/test-lists.git"

class ProgressPrinter(git.RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=""):
        print(op_code, cur_count, max_count, cur_count / (max_count or 100.0), message or "NO MESSAGE")

def clone_repo():
    git.Repo.clone_from(REPO_URL, REPO_DIR, branch="master")

def init_repo():
    if not os.path.exists(REPO_DIR):
        clone_repo()
    repo = git.Repo(REPO_DIR)
    repo.remote().fetch(progress=ProgressPrinter())
    return repo

class TestListManager:
    def __init__(self):
        self.repo = init_repo()

    def get_user_repo(self, username):
        repo_path = os.path.join(WORKING_DIR, "users", username)
        if not os.path.exists(repo_path):
            print(f"creating {repo_path}")
            self.repo.git.worktree("add", repo_path)
        return git.Repo(repo_path)

    def get_test_list(self, username):
        repo_path = os.path.join(WORKING_DIR, "users", username)
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

        filepath = os.path.join(WORKING_DIR, "users", username, "lists", f"{cc}.csv")

        with open(filepath, "a") as out_file:
            csv_writer = csv.writer(out_file, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            csv_writer.writerow(new_entry)
        repo.index.add([filepath])
        repo.index.commit(comment)

    def edit(self, username, cc, old_entry, new_entry, comment):
        repo = self.get_user_repo(username)

        filepath = os.path.join(WORKING_DIR, "users", username, "lists", f"{cc}.csv")

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

if __name__ == "__main__":
    main()
