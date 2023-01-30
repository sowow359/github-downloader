# Github downloader

### Usage

```
./github-downloader.py --home-folder ~/github/ --config github.conf
```

#### Config

Конфиг состоит из строк вида `{repo}, {n_releases_to_keep}, {release_type}`, где:
* repo - репозиторий гитхаба вида `org/repo`
* n_releases_to_keep - сколько релизов хранить локально
* release_type - (all|stable). all включает в себя релизы, помеченные флагом Pre-release

Пример:

```
apache/airflow, 2, all
will-stone/browserosaurus, 2, all
grafana/grafana, 2, all
prometheus/prometheus, 2, all
AdguardTeam/AdGuardHome, 2, all
```