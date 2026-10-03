<!--
SPDX-FileCopyrightText: 2020 - 2024 MDAD project contributors
SPDX-FileCopyrightText: 2020 - 2024 Slavi Pantaleev
SPDX-FileCopyrightText: 2020 Aaron Raimist
SPDX-FileCopyrightText: 2020 Chris van Dijk
SPDX-FileCopyrightText: 2020 Dominik Zajac
SPDX-FileCopyrightText: 2020 Mickaël Cornière
SPDX-FileCopyrightText: 2022 François Darveau
SPDX-FileCopyrightText: 2022 Julian Foad
SPDX-FileCopyrightText: 2022 Warren Bailey
SPDX-FileCopyrightText: 2023 Antonis Christofides
SPDX-FileCopyrightText: 2023 Felix Stupp
SPDX-FileCopyrightText: 2023 Pierre 'McFly' Marty
SPDX-FileCopyrightText: 2024 - 2025 Suguru Hirahara

SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Setting up MongoDB

This is an [Ansible](https://www.ansible.com/) role which installs [MongoDB](https://mongodb.com) to run as a [Docker](https://www.docker.com/) container wrapped in a systemd service.

MongoDB is a source-available cross-platform document-oriented (NoSQL) database program.

See the project's [documentation](https://www.mongodb.com/docs/) to learn what MongoDB does and why it might be useful to you.

## Adjusting the playbook configuration

To enable MongoDB with this role, add the following configuration to your `vars.yml` file.

**Note**: the path should be something like `inventory/host_vars/mash.example.com/vars.yml` if you use the [MASH Ansible playbook](https://github.com/mother-of-all-self-hosting/mash-playbook).

```yaml
########################################################################
#                                                                      #
# mongodb                                                              #
#                                                                      #
########################################################################

mongodb_enabled: true

# Put a strong password below, generated with `pwgen -s 64 1` or in another way
mongodb_root_password: ""

########################################################################
#                                                                      #
# /mongodb                                                             #
#                                                                      #
########################################################################
```

### Extending the configuration

There are some additional things you may wish to configure about the service.

Take a look at:

- [`defaults/main.yml`](../defaults/main.yml) for some variables that you can customize via your `vars.yml` file

## Installing

After configuring the playbook, run the installation command of your playbook as below:

```sh
ansible-playbook -i inventory/hosts setup.yml --tags=setup-all,start
```

If you use the MASH playbook, the shortcut commands with the [`just` program](https://github.com/mother-of-all-self-hosting/mash-playbook/blob/main/docs/just.md) are also available: `just install-all` or `just setup-all`

## Usage

After running the command for installation, the MongoDB instance becomes available.

### Getting a database terminal

You can use the CLI installed to the directory specified with `mongodb_bin_path` (default: `/mongodb/bin/`) for getting interactive terminal access using the MongoDB Shell [mongosh](https://www.mongodb.com/docs/mongodb-shell/).

By default, this tool puts you in the `admin` database, which contains nothing.

To see the available databases, run `show dbs`.

To change to another database (for example `infisical`), run `use infisical`.

To see the available tables in the current database, run `show tables`.

You can then proceed to write queries. Example: `db.users.find()`

>[!WARNING]
> Modifying the database directly (especially as services are running) is dangerous and may lead to irreversible database corruption. If you are not perfectly sure, create a backup!

## Maintenance

### Backing up a database

The script to dump the database (`dump-all`) is installed to the directory specified with `mongodb_bin_path`. It can dump the database to a path of your choosing.

Restoring a backup made this way can be done by importing it.

### Upgrading to a new major release

MongoDB refuses to start on data whose [feature compatibility version](https://www.mongodb.com/docs/manual/reference/command/setFeatureCompatibilityVersion/) (FCV) is not one its release accepts. To avoid MongoDB (and every service depending on it) going down after an upgrade, the role checks the FCV of the running MongoDB before installing anything:

- If the release about to be installed can start on it, nothing changes. The FCV is not raised any further, so that going back to the previous release remains possible.
- If the FCV can be raised (on the running MongoDB) to one the release about to be installed accepts, the role raises it to the lowest such value (unless `mongodb_fcv_auto_raise_enabled` is set to `false`).
- Otherwise, the role aborts while the currently installed MongoDB keeps running. This happens when upgrading across too many releases at once (it then tells you which release to go through first), or when downgrading to a release that cannot start on the current FCV.

Which FCVs each release can start on is described in `mongodb_startable_fcvs`. To force a specific FCV, set `mongodb_fcv`. To disable the check, set `mongodb_fcv_check_enabled: false`.

**Note**: whenever there is existing data, the check needs MongoDB to be running. If it is not, the role aborts and asks you to start it.

Taking a backup before upgrading to a new major release is still a good idea.

## Importing a database

The role supports importing **gzipped** MongoDB database dumps (created with `mongodump --gzip -o /directory`).

### Prerequisites

Before doing the actual import, your MongoDB dump file needs to be uploaded to the server (any path is okay).

### Importing a dump

Run this command to import the dump file. Make sure to replace `SERVER_PATH_TO_MONGODB_DUMP_DIRECTORY` with a file path on your server.

```sh
ansible-playbook -i inventory/hosts setup.yml --tags=import-mongodb --extra-vars=mongodb_server_path_dump=SERVER_PATH_TO_MONGODB_DUMP_DIRECTORY
```

>[!NOTE]
> `SERVER_PATH_TO_MONGODB_DUMP_DIRECTORY` must be a path to a **gzipped** MongoDB dump directory on the server (not on your local machine!)

## Troubleshooting

### Check the service's logs

You can find the logs in [systemd-journald](https://www.freedesktop.org/software/systemd/man/systemd-journald.service.html) by logging in to the server with SSH and running `journalctl -fu mongodb` (or how you/your playbook named the service, e.g. `mash-mongodb`).
