#!/usr/bin/env python3
##########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

"""Export an admitted v2 engine metrics contract as review artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    return parser.parse_args()


def _write_csv(path, fieldnames, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                name: ';'.join(value) if isinstance(value, list) else value
                for name, value in row.items()
            })


def main():
    args = _arguments()
    document = json.loads(args.contract.read_text(encoding='utf-8'))
    if document.get('schema') != 'cdeadmin.engine-metrics.v2':
        raise SystemExit(
            'Only an admitted v2 metrics contract may be exported'
        )
    args.output_directory.mkdir(parents=True, exist_ok=True)
    observations = document['native_observations']
    metrics = document['metrics']
    _write_csv(
        args.output_directory / 'native_observations.csv',
        list(observations[0]), observations,
    )
    _write_csv(
        args.output_directory / 'operational_metrics.csv',
        list(metrics[0]), metrics,
    )
    summary = (
        f"# {document['engine_id'].title()} native metrics inventory\n\n"
        f"Reference version: {document['reference_version']}  \n"
        f"Contract: `{document['contract_id']}`  \n"
        f"Native observations: {len(observations)}  \n"
        f"Operational metrics: {len(metrics)}  \n\n"
        "`native_observations.csv` is the complete discovered native field "
        "surface. `operational_metrics.csv` contains only observations with "
        "authoritatively documented quantitative semantics. Identifiers, "
        "text, timestamps, plans, status values, and configuration attributes "
        "remain observations and are not falsely advertised as metrics.\n"
    )
    (args.output_directory / 'README.md').write_text(
        summary, encoding='utf-8'
    )


if __name__ == '__main__':
    main()
