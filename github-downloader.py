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
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, urlretrieve

GITHUB_API_BASE_URL = "https://api.github.com/repos"


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
    return parser.parse_args()


@run_once_per(seconds=5)
def get_as_json(url):
    print(f"GET: {url}")
    req = Request(
        url=url,
        data=None,
        headers={
            "Accept": "application/vnd.github+json",
            # "Authorization": "Bearer <YOUR-TOKEN>",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(
                req,
                timeout=3,
        ) as response:
            print(response.getcode())
            content = response.read().decode("utf-8")
            return json.loads(content)

    except HTTPError as e:
        print(f"Error: {e.code} {e.msg}")


def get_latest(repo: str):
    print(f"Requesting latest release for {repo}")
    url = f"{GITHUB_API_BASE_URL}/{repo}/releases/latest"
    js = get_as_json(url)
    if js:
        js["tag_name"] = js["tag_name"].replace('/', '_')
        return js


def get_releases(repo: str, release_type: str) -> dict:
    # https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases
    # всегда по убыванию, т.е. как на странице релизов

    all_releases = OrderedDict()
    latest = get_latest(repo)

    if not latest:
        return {}

    # always keep one tagged with `latest` first
    all_releases[latest["tag_name"]] = latest

    params = {"per_page": "50"}
    encoded_params = urlencode(params)

    url = f"{GITHUB_API_BASE_URL}/{repo}/releases?{encoded_params}"
    print(f"Requesting github releases for {repo}")
    content = get_as_json(url)

    for release in content:
        release["tag_name"] = release["tag_name"].replace('/', '_')
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
        print(f"Repo folder does not exists, creating {repo_dir}")
        Path(repo_dir).mkdir(parents=True, exist_ok=True)
        return []

    versions = os.listdir(repo_dir)
    print(f"Got {len(versions)} local versions: {versions}")
    return list(sorted(versions, reverse=True))


def download(release_info: dict, home: str, repo: str):
    release_path = os.path.join(home, repo, release_info["tag_name"])
    if not os.path.exists(release_path):
        os.mkdir(release_path)
        print(f"Created release dir {release_path}")

    for asset in release_info["assets"]:
        url = asset["browser_download_url"]
        asset_name = asset["name"]

        filepath = os.path.join(release_path, asset_name)
        print(f"Downloading {asset['name']} to {filepath}")

        urlretrieve(url, filepath, reporthook)

    tarball_path = os.path.join(release_path, "source.tar.gz")
    print(f"Downloading source tarball to {tarball_path}")
    urlretrieve(release_info["tarball_url"], tarball_path, reporthook)

    zipball_path = os.path.join(release_path, "source.zip")
    print(f"Downloading source zipball to {zipball_path}")
    urlretrieve(release_info["zipball_url"], zipball_path, reporthook)

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


def drop(home: str, repo: str, version: str):
    release_path = os.path.join(home, repo, version)
    shutil.rmtree(release_path)


def run(home: str, repo: str, n_releases: int, release_type: str):
    github_releases = get_releases(repo=repo, release_type=release_type)
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
        print("No local releases found")
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
        drop(home=home, repo=repo, version=version)

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
            print(f"Sleeping for 5 seconds")
            time.sleep(5)


if __name__ == '__main__':
    main()
