#!/usr/bin/env python3

import argparse
import inspect
import json
import os
import shutil
import time
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from urllib import request

GITHUB_API_BASE_URL = "https://api.github.com/repos"


def run_once_per(seconds):
    """
    Allows function to run again only after specified number of seconds.
    """

    last_run: float = float('-inf')

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
    parser.add_argument("--config", action="store", type=str, required=False, dest="config_file_path")
    return parser.parse_args()


@run_once_per(seconds=2)
def get(url):
    print(f"GET: {url}")
    req = request.Request(
        url=url,
        data=None,
        headers={
            "Accept": "application/vnd.github+json",
            # "Authorization": "Bearer <YOUR-TOKEN>",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with request.urlopen(
        req,
        timeout=3,
    ) as response:
        print(response.getcode())
        content = response.read().decode("utf-8")
    return content


def get_latest(repo: str):
    print(f"Requesting latest release for {repo}")
    url = f"{GITHUB_API_BASE_URL}/{repo}/releases/latest"
    js = json.loads(get(url))
    js["tag_name"] = js["tag_name"].replace('/', '_')
    return js


def get_releases(repo: str, release_type: str) -> dict:
    # https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases
    # всегда по убыванию, т.е. как на странице релизов

    params = {"per_page": "50"}
    encoded_params = urllib.parse.urlencode(params)

    url = f"{GITHUB_API_BASE_URL}/{repo}/releases?{encoded_params}"
    print(f"Requesting github releases for {repo}")
    content = get(url)

    all_releases = OrderedDict(
        (release["tag_name"].replace('/', '_'), release)
        for release in json.loads(content)  # for '/' in tag
    )

    for release in all_releases.values():
        release["tag_name"] = release["tag_name"].replace('/', '_')

    return (
        all_releases
        if release_type == "all"
        else OrderedDict(
            (k, v)
            for k, v in all_releases.items()
            if not v["prerelease"]
        )
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

        request.urlretrieve(url, filepath, reporthook)

    tarball_path = os.path.join(release_path, "source.tar.gz")
    print(f"Downloading source tarball to {tarball_path}")
    request.urlretrieve(release_info["tarball_url"], tarball_path, reporthook)

    zipball_path = os.path.join(release_path, "source.zip")
    print(f"Downloading source zipball to {zipball_path}")
    request.urlretrieve(release_info["zipball_url"], zipball_path, reporthook)

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
    while len(github_releases) > n_releases:
        github_releases.popitem()
    releases_to_keep = github_releases

    # always keep one tagged with `latest`
    latest = get_latest(repo)
    if latest["tag_name"] not in releases_to_keep:
        print(f"Keeping latest release {latest['tag_name']}")
        releases_to_keep[latest["tag_name"]] = latest

    n_releases = len(releases_to_keep)

    local_versions = set(get_local_versions(home=home, repo=repo))
    if not local_versions:
        # no local versions, just download last `n_releases` releases from github
        print("No local releases found")
        print(f"{n_releases} will be downloaded")
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
        line.strip('\n').split(', ')
        for line in lines
    ]

    for i, (repo, n_releases, release_type) in enumerate(conf):
        assert release_type in ["all", "stable"], f"Unknown release type `{release_type}` given. Use `all` or `stable`"
        run(
            home=args.home,
            repo=repo.strip('/'),
            n_releases=int(n_releases),
            release_type=release_type
        )

        if i != len(conf) - 1:
            print(f"Sleeping for 5 seconds")
            time.sleep(5)


if __name__ == '__main__':
    main()
