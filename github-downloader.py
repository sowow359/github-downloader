#!/usr/bin/env python3

import argparse
import inspect
import json
import os
import shutil
import time
import urllib.parse
from pathlib import Path
from urllib import request

GITHUB_API_BASE_URL = "https://api.github.com/repos"

def get_args():
    parser = argparse.ArgumentParser(prog="Github downloader")
    parser.add_argument("--home-folder", action="store", type=str, required=True, dest="home")
    parser.add_argument("--config", action="store", type=str, required=False, dest="config_file_path")
    return parser.parse_args()


def get_and_filter_github_releases(repo: str, release_type: str):
    # https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases
    # всегда по убыванию, т.е. как на странице релизов

    params = {
        "per_page": "50"
    }
    encoded_params = urllib.parse.urlencode(params)

    url = f"{GITHUB_API_BASE_URL}/{repo}/releases?{encoded_params}"
    print(f"Requesting github releases for {repo}")
    print(f"URL: {url}")

    req = request.Request(
        url=url,
        data=None,
        headers={
            "Accept": "application/vnd.github+json",
            # "Authorization": "Bearer <YOUR-TOKEN>",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with request.urlopen(req, timeout=3, ) as response:
        print(response.getcode())
        content = response.read().decode("utf-8")

    js = json.loads(content)

    if release_type == "stable":
        return [
            item
            for item in js
            if not item["prerelease"]
        ]
    else:
        return js


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

        request.urlretrieve(url, filepath)

    tarball_path = os.path.join(release_path, "source.tar.gz")
    print(f"Downloading source tarball to {tarball_path}")
    request.urlretrieve(release_info["tarball_url"], tarball_path)

    zipball_path = os.path.join(release_path, "source.zip")
    print(f"Downloading source zipball to {zipball_path}")
    request.urlretrieve(release_info["zipball_url"], zipball_path)

    print(f"Creating release README.md file")
    with open(os.path.join(release_path, "README.md"), "w") as f:
        content = inspect.cleandoc(
            f"""
# {release_info['name']}
        
Github Release link: {release_info['html_url']}

created_at = {release_info['created_at']}

published_at = {release_info['published_at']}

# Release notes

{release_info["body"]}
        """
        )
        f.write(content)


def drop(home: str, repo: str, version: str):
    release_path = os.path.join(home, repo, version)
    shutil.rmtree(release_path)

def run(home: str, repo: str, n_releases: int, release_type: str):
    github_releases = get_and_filter_github_releases(repo=repo, release_type=release_type)
    releases_to_keep = github_releases[:n_releases]

    # someone likes to put '/' in tags
    for release in releases_to_keep:
        release["tag_name"] = release["tag_name"].replace('/', '_')

    local_versions = set(get_local_versions(home=home, repo=repo))

    if not local_versions:
        # no local versions, just download last `n_releases` releases from github
        print("No local releases found")
        print(f"{n_releases} will be downloaded")
        for i, new in enumerate(releases_to_keep[:n_releases]):
            print(f"Processing release {i + 1}/{n_releases}. {repo}:{new['tag_name']}")
            download(release_info=new, home=home, repo=repo)
        print("Done")
        return

    versions_to_keep = {
        item["tag_name"]
        for item in releases_to_keep
    }
    versions_to_download = versions_to_keep - local_versions
    versions_to_delete = local_versions - versions_to_keep

    releases_to_download = [
        release
        for release in releases_to_keep
        if release['tag_name'] in versions_to_download
    ]

    print(f"Releases to download: {releases_to_download}")

    for i, new in enumerate(releases_to_download):
        print(f"Downloading release {i + 1}/{len(releases_to_download)}. {repo}:{new['tag_name']}")
        download(release_info=new, home=home, repo=repo)

    for version in versions_to_delete:
        print(f"Removing {repo}: {version}")
        drop(home=home, repo=repo, version=version)


def main():
    args = get_args()
    with open(args.config_file_path, 'r') as f_conf:
        lines = f_conf.readlines()

    conf = [
        line.strip('\n').split(', ')
        for line in lines
    ]

    for repo, n_releases, release_type in conf:
        run(
            home=args.home,
            repo=repo.strip('/'),
            n_releases=int(n_releases),
            release_type=release_type
        )
        print(f"Sleeping for 10 seconds")
        time.sleep(10)

if __name__ == '__main__':
    main()
