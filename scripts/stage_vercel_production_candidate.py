"""Prepare an OFFLINE production source candidate, not a deploy/link/import."""
import argparse
from pathlib import Path
from stage_vercel_preview import stage, copy_file, ROOT


def prepare(destination):
    stage(destination)
    copy_file(ROOT/'deploy/vercel-production/app.py',destination/'app.py')
    copy_file(ROOT/'deploy/vercel-production.example.json',destination/'vercel.json')
    # Preserve the already-published update endpoint without importing the
    # developer plugin repository, old archives, or private history.
    for name in ('manifest.json','papermind-connector.xpi'):
        copy_file(ROOT/'zotero-plugin'/name,destination/'zotero-plugin'/name)
    with (destination/'.vercelignore').open('a') as stream:
        stream.write('\n!zotero-plugin\n')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    print(prepare(parser.parse_args().destination))
