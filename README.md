# WordPress fetching script

Fetches configured WordPress content, PMPro membership levels, and MEC calendar
occurrences. Each run writes JSON source files and a manifest to local storage or
Amazon S3.

## Local run

Requires Python 3.9 or newer with `venv` support.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
# Fill in .env, then:
.venv/bin/python -m app.jobs.run_ingestion
```

`MAX_CALENDAR_EVENTS=all` (or a blank value) resolves all MEC event definitions.
Set it to a small positive integer for a trial run.

## EC2 deployment

1. Put the repository at `/opt/fetching-script`, owned by the account that will
   run it. The supplied unit uses `ec2-user`; change `User` and `Group` to
   `ubuntu` when using an Ubuntu AMI.
2. Create the virtual environment on EC2. Do not copy the development machine's
   `.venv` directory because virtual environments are platform-specific.

   ```bash
   cd /opt/fetching-script
   python3 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `/etc/fetching-script.env`, fill in its values, and
   restrict it:

   ```bash
   sudo cp .env.example /etc/fetching-script.env
   sudo chmod 600 /etc/fetching-script.env
   ```

4. For S3 storage, attach an EC2 instance role. It needs `s3:ListBucket` on the
   bucket (used by the startup check) and `s3:PutObject` on
   `arn:aws:s3:::BUCKET_NAME/raw/*`. Static AWS access keys are not needed.
5. Install and test the service:

   ```bash
   sudo cp deploy/fetching-script.service /etc/systemd/system/
   sudo cp deploy/fetching-script.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl start fetching-script.service
   sudo journalctl -u fetching-script.service -n 100 --no-pager
   ```

6. Once the manual run succeeds, enable the daily 02:00 UTC timer:

   ```bash
   sudo systemctl enable --now fetching-script.timer
   systemctl list-timers fetching-script.timer
   ```

The instance needs outbound HTTPS access to WordPress and S3. It does not need
an inbound application port for this job.

## Verification

Run the offline test suite without contacting WordPress or AWS:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

A successful run returns exit code zero and writes a manifest with status
`complete` or `complete_with_errors`. A fatal source error returns a non-zero
exit code and attempts to write a manifest with status `failed`.
