# files.musial.io short links

This service resolves durable short links under `https://files.musial.io/s/`.
The existing load balancer routes `/s/*` to this Cloud Run service and continues
to route every other path to the `musial-family-public` GCS backend bucket.

Firestore database `files-shortlinks` is the source of truth. Cloud CDN caches
successful `308 Permanent Redirect` responses; unknown and disabled links are
never cached. Slugs are immutable and are not recycled after being disabled.

## Add a link

Use the management command from an authenticated workstation:

```sh
apps/files-shortlinks/files-link add assessing-vertical-coherence \
  'gs://musial-family-public/evan/meteorology/papers/Evan Musial - Assessing Vertical Coherence of Doppler Radar Rotation Signatures (2026).pdf'
```

The command verifies the object, records its generation and metadata, creates
an append-only audit event, and verifies both public forms:

```text
https://files.musial.io/s/assessing-vertical-coherence
https://files.musial.io/s/assessing-vertical-coherence.pdf
```

Other commands:

```sh
apps/files-shortlinks/files-link get assessing-vertical-coherence
apps/files-shortlinks/files-link list
apps/files-shortlinks/files-link verify assessing-vertical-coherence
apps/files-shortlinks/files-link disable assessing-vertical-coherence
```

`disable` creates a tombstone and invalidates both CDN entries. There is no
retarget or delete command: changing a permalink requires an explicit code and
operations decision.

## Runtime configuration

| Setting | Value |
| --- | --- |
| Project | `musial-family-public` |
| Firestore database | `files-shortlinks` |
| Firestore collection | `short_links` |
| Audit collection | `short_link_events` |
| Cloud Run region | `us-east4` |
| Canonical host | `files.musial.io` |

The deployed service is `files-shortlinks`, backed by global backend service
`files-shortlinks-backend` and serverless NEG `files-shortlinks-neg` in
`us-east4`. The production revision uses one warm instance, a maximum of ten,
and dedicated read-only service account
`files-shortlinks-runtime@musial-family-public.iam.gserviceaccount.com`. Its
Firestore IAM grant is conditioned on the exact `files-shortlinks` database.

The Firestore database uses multi-region `nam7`, deletion protection, seven-day
point-in-time recovery, daily backups retained for seven days, and weekly
backups retained for fourteen weeks.

## URL-map validation and rollback

The intended URL map and its static route tests are saved in
[`infra/url-map.yaml`](infra/url-map.yaml). Validate without changing GCP:

```sh
gcloud compute url-maps validate \
  --source=apps/files-shortlinks/infra/url-map.yaml \
  --load-balancing-scheme=EXTERNAL --global \
  --project=musial-family-public
```

The pre-short-link URL map is retained in
[`infra/url-map-before-shortlinks.yaml`](infra/url-map-before-shortlinks.yaml).
Importing that file is the explicit routing rollback; it removes `/s/*` from
Cloud Run while leaving the service, Firestore registry, and GCS files intact.

## Local tests

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
```

## Deploy a tested revision

After the local tests pass, build and deploy by immutable image digest:

```sh
apps/files-shortlinks/infra/deploy.sh
```

The initial known-good production revision is `files-shortlinks-r20261023-1`,
using image digest
`sha256:b2b4e9bd2f31b2d586c0b7b37b814e97ab13441a99facd2227ea060b76de4e4d`.
Runtime rollback can direct all traffic back to that revision without changing
the load balancer or Firestore registry.
