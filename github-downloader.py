#!/usr/bin/env python3

import argparse
import inspect
import json
import os
import re
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from urllib.error import HTTPError, ContentTooShortError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, urlretrieve

GITHUB_API_BASE_URL = "https://api.github.com/repos"


class BaseError(Exception):
    def __init__(self, message: str):
        self.message = message

    def __str__(self):
        return f"{self.__class__.__name__}: {self.message}"


class DownloadError(BaseError):
    pass


def run_once_per(seconds):
    """
    Allows function to run again only after specified number of seconds.
    """

    last_run = float('-inf')

    def decorator(function):
        def wrapper(*args, **kwargs):
            nonlocal last_run

            passed = time.time() - last_run
            time.sleep(max(seconds - passed, 0))

            result = function(*args, **kwargs)
            last_run = time.time()
            return result

        return wrapper

    return decorator


def reporthook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if downloaded < total_size:
        print(int(downloaded / total_size * 100), '%', end="\r", sep='')


def get_args():
    parser = argparse.ArgumentParser(prog="Github downloader")
    parser.add_argument("--home-folder", action="store", type=str, required=True, dest="home")
    parser.add_argument("--config", action="store", type=str, required=True, dest="config_file_path")
    parser.add_argument(
        "--sleep-between-repos", action="store", type=int, required=False, dest="sleep_between_repos", default=5
    )
    return parser.parse_args()


@run_once_per(seconds=5)
def get_as_json(url):
    print(f"GET: {url}")
    req = Request(
        url=url,
        data=None,
        headers={
            "Accept": "application/vnd.github+json",
            # "Authorization": "Bearer <YOUR-TOKEN>",  # TODO pass with bcrypt
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(
            req,
            timeout=3,
    ) as response:
        content = response.read().decode("utf-8")
        return json.loads(content)


def get_latest(repo: str):
    try:
        latest = get_as_json(f"{GITHUB_API_BASE_URL}/{repo}/releases/latest")
    except HTTPError as e:
        if e.code == 404:
            # no releases
            return {}
        else:
            raise e

    latest["tag_name"] = latest["tag_name"].replace('/', '_')
    return latest


def get_last_n_releases(repo: str, n: int = 50):
    params = {"per_page": n}
    encoded_params = urlencode(params)

    releases = get_as_json(f"{GITHUB_API_BASE_URL}/{repo}/releases?{encoded_params}")

    for release in releases:
        release["tag_name"] = release["tag_name"].replace('/', '_')
    return releases


def get_releases(repo: str, release_type: str) -> dict:
    # https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases

    all_releases = OrderedDict()

    latest_release = get_latest(repo)
    if not latest_release:
        # no latest release -> no releases
        return {}
    # always keep one tagged with `latest` first
    all_releases[latest_release["tag_name"]] = latest_release

    last_n = get_last_n_releases(repo)
    for release in last_n:
        all_releases[release["tag_name"]] = release

    return (
        all_releases
        if release_type == "all"
        else OrderedDict((k, v) for k, v in all_releases.items() if not v["prerelease"])
    )


def get_local_versions(home: str, repo: str):
    print(f"Listing existing local versions")
    repo_dir = os.path.join(home, repo)
    if not os.path.exists(repo_dir):
        return []

    versions = os.listdir(repo_dir)
    print(f"Got {len(versions)} local versions: {versions}")
    return list(sorted(versions, reverse=True))


@run_once_per(seconds=5)
def download_file(url: str, to: str):
    n_retries = 3
    for i in range(n_retries):
        try:
            urlretrieve(url, to, reporthook)
            return
        except ContentTooShortError as e:
            print(f"Error: {e}")
            if os.path.exists(to):
                os.remove(to)

        if i != n_retries - 1:
            print(f"Attempt {i + 1}/{n_retries}")

    raise DownloadError(f"Error downloading {url}")


def download(release_info: dict, home: str, repo: str):
    release_path = os.path.join(home, repo, release_info["tag_name"])
    try:
        _download_release(release_info=release_info, release_path=release_path)
        return
    except DownloadError as de:
        print(f"Failed to download release due to network error: {de}")
    except KeyboardInterrupt:
        print()
        print(f"Interrupted")
    except Exception as ex:
        print(f"Unexpected error: {ex}")

    print(f"Cleaning up: removing {release_path}")
    shutil.rmtree(release_path)
    exit(1)


def _download_release(release_info: dict, release_path: str):
    if not os.path.exists(release_path):
        Path(release_path).mkdir(parents=True)
        print(f"Created release dir {release_path}")

    for asset in release_info["assets"]:
        url = asset["browser_download_url"]
        asset_name = asset["name"]

        filepath = os.path.join(release_path, asset_name)
        print(f"Downloading {asset['name']} to {filepath}")
        download_file(url, filepath)

    tarball_path = os.path.join(release_path, "source.tar.gz")
    print(f"Downloading source tarball to {tarball_path}")
    download_file(release_info["tarball_url"], tarball_path)

    zipball_path = os.path.join(release_path, "source.zip")
    print(f"Downloading source zipball to {zipball_path}")
    download_file(release_info["zipball_url"], zipball_path)

    print(f"Creating release README.md file")
    with open(os.path.join(release_path, "README.md"), "w") as f:
        content = inspect.cleandoc(
            f"""
                # {release_info['name']}
                        
                Github Release link: {release_info['html_url']}
                
                created_at = {release_info['created_at']}
                
                published_at = {release_info['published_at']}
                
                # Release notes
        """
        )
        f.write(content)
        f.write('\n\n')
        f.write(release_info["body"])


def run(home: str, repo: str, n_releases: int, release_type: str):
    try:
        github_releases = get_releases(repo=repo, release_type=release_type)
    except HTTPError as he:
        print(f"Error getting releases for {repo}: {he}")
        print("Exiting")
        exit(1)

    if not github_releases:
        print(f"No github releases found, skipping {repo}")
        return

    while len(github_releases) > n_releases:
        github_releases.popitem()
    releases_to_keep = github_releases

    n_releases = len(releases_to_keep)

    local_versions = set(get_local_versions(home=home, repo=repo))
    if not local_versions:
        # no local versions, just download last `n_releases` releases from github
        print(f"{n_releases} will be downloaded: {releases_to_keep.keys()}")
        for i, new in enumerate(releases_to_keep.values()):
            print(f"Processing release {i + 1}/{n_releases}. {repo}:{new['tag_name']}")
            download(release_info=new, home=home, repo=repo)
        print("Done")
        return

    versions_to_download = releases_to_keep.keys() - local_versions
    print(f"About to download: {versions_to_download}")

    versions_to_delete = local_versions - releases_to_keep.keys()
    print(f"Will be deleted: {versions_to_delete}")

    for i, version in enumerate(versions_to_download):
        release = releases_to_keep[version]
        print(f"Downloading release {i + 1}/{len(versions_to_download)}. {repo}:{release['tag_name']}")
        download(release_info=release, home=home, repo=repo)

    for version in versions_to_delete:
        print(f"Removing {repo}: {version}")
        release_path = os.path.join(home, repo, version)
        shutil.rmtree(release_path)

    print("Done")


def main():
    args = get_args()
    with open(args.config_file_path, 'r') as f_conf:
        lines = f_conf.readlines()

    conf = [
        re.split(r',\s*', line.strip('\n'))
        for line in lines
    ]

    for i, (repo, n_releases, release_type) in enumerate(conf):
        assert release_type in ["all", "stable"], f"Unknown release type `{release_type}` given. Use `all` or `stable`"

        n_releases = int(n_releases)
        assert (
                n_releases > 0
        ), f"Incorrect number of releases for repo {repo}, number should be positive, `{n_releases}` given"

        run(home=args.home, repo=repo.strip('/'), n_releases=n_releases, release_type=release_type)

        if i != len(conf) - 1:
            print(f"Sleeping for {args.sleep_between_repos} seconds")
            time.sleep(args.sleep_between_repos)


if __name__ == '__main__':
    main()
