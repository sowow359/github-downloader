#!/usr/bin/env python3

import argparse
import inspect
import json
import os
import re
import shutil
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.error import HTTPError, ContentTooShortError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, urlretrieve

socket.setdefaulttimeout(10)

GITHUB_API_BASE_URL = "https://api.github.com/repos"

PRERELEASE = "prerelease"
STABLE = "stable"


@dataclass
class AssetInfo:
    size: int
    name: str
    url: Optional[str]

    @classmethod
    def from_dict(cls, d: dict):
        url = d["browser_download_url"]
        asset_name = d["name"]
        size = d["size"]

        return cls(
            name=asset_name,
            url=url,
            size=size
        )


@dataclass
class ReleaseInfo:
    tag: str
    assets: List[AssetInfo]
    readme: str
    release_type: str
    zipball_url: str
    tarball_url: str
    created_at: str

    @classmethod
    def from_dict(cls, d: dict):
        assets = [AssetInfo.from_dict(info) for info in d["assets"]]

        readme = inspect.cleandoc(
            f"""
                # {d['name']}
                        
                Github Release link: {d['html_url']}
                
                created_at = {d['created_at']}
                
                published_at = {d['published_at']}
                
                # Release notes
        """
        ) + d.get("body", "No release notes were provided by developers")
        return cls(
            tag=d["tag_name"].replace('/', '_'),
            assets=assets,
            readme=readme,
            release_type=PRERELEASE if d["prerelease"] else STABLE,
            zipball_url=d["zipball_url"],
            tarball_url=d["tarball_url"],
            created_at=d['created_at'],
        )


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


def reporthook(count, block_size, total_size):
    global start_time
    if count == 0:
        start_time = time.time()
        return
    duration = time.time() - start_time
    downloaded = int(count * block_size)
    speed = int(downloaded / (1024 * 1024 * duration))

    percent = min(int(count * block_size * 100 / total_size), 100) if total_size >= 0 else '...'

    print(
        f"\r{percent}%, "
        f"{downloaded / (1024 * 1024):.2f} MB, "
        f"{speed:.1f} MB/s, "
        f"{duration:.1f} seconds passed",
        end=''
    )
    sys.stdout.flush()


def get_args():
    parser = argparse.ArgumentParser(prog="Github downloader")
    parser.add_argument(
        "--home-folder",
        action="store",
        type=str,
        required=True,
        dest="home",
        help="Directory for downloading releases",
    )
    parser.add_argument(
        "--config",
        action="store",
        type=str,
        required=True,
        dest="config_file_path",
        help="Config filename/path. Consists of lines `{repo}, {release_number}, {release_type}`. "
             "See https://github.com/sowow359/github-downloader#config for more info"
    )
    parser.add_argument(
        "--sleep-between-repos",
        action="store",
        type=int,
        required=False,
        dest="sleep_between_repos",
        default=5,
        help="How many seconds must pass between repo processing, 5 by default"
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
    ) as response:
        content = response.read().decode("utf-8")
        return json.loads(content)


def get_latest(repo: str) -> Optional[ReleaseInfo]:
    try:
        latest = get_as_json(f"{GITHUB_API_BASE_URL}/{repo}/releases/latest")
    except HTTPError as e:
        if e.code == 404:
            # no releases
            return None
        else:
            raise e

    return ReleaseInfo.from_dict(latest)


def get_last_n_releases(repo: str, n: int = 50) -> List[ReleaseInfo]:
    params = {"per_page": n}
    encoded_params = urlencode(params)

    releases = get_as_json(f"{GITHUB_API_BASE_URL}/{repo}/releases?{encoded_params}")

    for release in releases:
        release["tag_name"] = release["tag_name"].replace('/', '_')

    return [ReleaseInfo.from_dict(d) for d in releases]


def get_releases(repo: str, release_type: str) -> List[ReleaseInfo]:
    # https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases

    latest_release = get_latest(repo)
    if not latest_release:
        # no latest release -> no releases
        return []

    last_n: list[ReleaseInfo] = get_last_n_releases(repo)

    all_of_them: list[ReleaseInfo] = [latest_release] + [r for r in last_n if r.tag != latest_release.tag]
    result: list[ReleaseInfo] = (
        all_of_them
        if release_type == "all"
        else list(filter(lambda r: r.release_type == STABLE, all_of_them))
    )

    return result


def get_local_versions(home: str, repo: str) -> List[str]:
    print(f"Listing existing local versions")
    repo_dir = os.path.join(home, repo)
    if not os.path.exists(repo_dir):
        return []

    versions = [item for item in os.listdir(repo_dir) if not item.startswith('.')]
    return list(sorted(versions, reverse=True))


@run_once_per(seconds=3)
def download_file(url: str, to: str):
    def cleanup(path):
        print(f"Cleaning up {path}")
        if os.path.exists(path):
            os.remove(path)

    def sigterm_handler(_signo, _stack_frame):
        print("\nReceived SIGTERM while downloading file")
        cleanup(to)
        raise OSError("Received SIGTERM")

    def empty_handler(_signo, _stack_frame):
        raise OSError("\nReceived SIGTERM")

    signal.signal(signal.SIGTERM, sigterm_handler)

    n_retries = 3
    for i in range(n_retries):
        try:
            signal.signal(signal.SIGTERM, sigterm_handler)
            urlretrieve(url, to, reporthook)
            signal.signal(signal.SIGTERM, empty_handler)
            print()
            return
        except (ContentTooShortError, TimeoutError) as e:
            print(f"\nError: {e}")

        cleanup(to)

        if i != n_retries - 1:
            print(f"Attempt {i + 1}/{n_retries}")

    raise DownloadError(f"Error downloading {url}")


def download(release_info: ReleaseInfo, home: str, repo: str):
    release_path = os.path.join(home, repo, release_info.tag)
    try:
        _download_release(release_info=release_info, release_path=release_path)
        return
    except DownloadError as de:
        print(f"Failed to download release due to network error: {de}")
    except KeyboardInterrupt:
        print(f"\nInterrupted")
    except Exception as ex:
        print(f"Unexpected error: {ex.__class__.__name__}: {ex}")
    exit(1)


def _download_release(release_info: ReleaseInfo, release_path: str):
    if not os.path.exists(release_path):
        Path(release_path).mkdir(parents=True)
        print(f"Created release dir {release_path}")

    existing_assets_with_sizes = {
        n: os.path.getsize(os.path.join(release_path, n))
        for n in os.listdir(release_path)
        if n not in ("README.md", "source.zip", "source.tar.gz")
    }

    release_assets_with_sizes = {
        i.name: i.size
        for i in release_info.assets
    }

    to_download_names = [
        name
        for name, size in release_assets_with_sizes.items()
        if (
                name not in existing_assets_with_sizes
                or size != existing_assets_with_sizes[name]
        )
    ]

    to_download_assets = [
        i
        for i in release_info.assets
        if i.name in to_download_names
    ]

    for asset in to_download_assets:
        filepath = os.path.join(release_path, asset.name)
        print(f"Downloading {asset.name} to {filepath}")
        download_file(asset.url, filepath)

    tarball_filename = "source.tar.gz"
    tarball_path = os.path.join(release_path, tarball_filename)
    if not os.path.exists(tarball_path):
        print(f"Downloading source tarball to {tarball_path}")
        download_file(release_info.tarball_url, tarball_path)

    zipball_filename = "source.zip"
    zipball_path = os.path.join(release_path, zipball_filename)
    if not os.path.exists(zipball_path):
        print(f"Downloading source zipball to {zipball_path}")
        download_file(release_info.zipball_url, zipball_path)

    readme_filename = "README.md"
    readme_path = os.path.join(release_path, readme_filename)
    if not os.path.exists(readme_path):
        print(f"Creating release README.md file")
        with open(readme_path, "w") as f:
            f.write(release_info.readme)

    print("All good")


def run(home: str, repo: str, n_releases: int, release_type: str):
    try:
        github_releases: list[ReleaseInfo] = get_releases(repo=repo, release_type=release_type)[:n_releases]
    except HTTPError as he:
        print(f"Error getting releases for {repo}: {he}")
        print("Exiting")
        exit(1)

    if not github_releases:
        print(f"No github releases found, skipping {repo}")
        return

    releases_to_keep = list(sorted(github_releases, key=lambda x: x.created_at, reverse=True))

    local_versions = set(get_local_versions(home=home, repo=repo))
    print(f"Got {len(local_versions)} local versions: {local_versions}")

    if not local_versions:
        # no local versions, just download last `n_releases` releases from GitHub
        print(f"{n_releases} will be downloaded: {[item.tag for item in releases_to_keep]}")
        for i, new in enumerate(releases_to_keep):
            print(f"Processing release {i + 1}/{n_releases}. {repo}:{new.tag}")
            download(release_info=new, home=home, repo=repo)
        print("Done")
        return

    tags_to_keep: set[str] = set(r.tag for r in releases_to_keep)
    print(f"Should remain: {tags_to_keep}")

    versions_to_delete: set[str] = local_versions - tags_to_keep
    if versions_to_delete:
        print(f"Will be deleted: {versions_to_delete}")

    for i, release in enumerate(releases_to_keep):
        print(f"Processing release {repo}:{release.tag}")  # {i + 1}/{len(versions_to_download)}
        download(release_info=release, home=home, repo=repo)

    for version in versions_to_delete:
        print(f"Removing {repo}: {version}")
        release_path = os.path.join(home, repo, version)
        shutil.rmtree(release_path)

    print(f"Done with {repo}")


def main():
    args = get_args()
    with open(args.config_file_path, 'r') as f_conf:
        lines = f_conf.readlines()

    conf = [
        re.split(r',\s*', line.strip('\n'))
        for line in lines
    ]
    conf = list(filter(lambda x: len(x) == 3, conf))

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
