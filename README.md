# Github downloader

Tool for stashing locally last N releases from your favorite repos. 
Release marked as `latest` has top priority, will be downloaded first. Other N-1 releases are selected by publish date.

No need for github token.

### Usage

```
./github-downloader.py --home-folder ~/github/ --config github.conf [--sleep-between-repos N]
```

* `home-folder` - where to stash releases with assets
* `config` - see below
* `sleep-between-repos` - how many seconds to sleep between repos, 5 by default

#### Config

Consists of lines `{repo}, {release_number}, {release_type}`
* repo - Well, repo. Looks like `{owner}/{repo}`
* release_number - How many releases to store for given repo.
* release_type - `(all|stable)`. `all` includes releases marked as `Pre-release`

```
apache/airflow, 2, all
will-stone/browserosaurus, 2, all
grafana/grafana, 2, stable
prometheus/prometheus, 2, stable
AdguardTeam/AdGuardHome, 2, stable
```