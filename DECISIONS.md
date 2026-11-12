# Test OpenAI Decisions on saved images

Use the motion detector's existing environment; no extra packages are needed.
Keep the camera capture running in its tmux session and use a second terminal.
Export `OPENAI_API_KEY` in that second terminal. Environment variables set in
another terminal are not automatically shared.

Preview the selection without using the API:

```bash
cd ~/cat-deterrent
source .venv/bin/activate
python decisions_detector.py captures-tc41 --limit 5 --dry-run
```

Then upload those images for analysis:

```bash
python decisions_detector.py captures-tc41 --limit 5
```

This uploads at most five images, one request per image, to OpenAI and incurs API
usage. Selection is in filename order, which puts these timestamped captures in
chronological order. You can supply a single image path instead. There are no
automatic retries; another invocation analyses the selected images again and
incurs usage again. Change `--limit` explicitly to test more images.

The script asks whether a cat, person, and other animal are visible. Each printed
number is the model's estimated probability, not a validated accuracy measure.
`REFUSED` means there was no classification for that question. A low cat score
does not prove absence. No decision threshold or actuator control is implemented.

Each invocation creates a separate directory under `decisions-results/`. JSON
reports link to the original image and retain questions, full API response,
token usage when returned, and request duration. They do not store the API key or
base64 request image. Original captures are unchanged. HTTP errors stop the run;
completed reports remain available. For HTTP 429, check billing/quota and rate
limits; for 401, check the key; for 403, check permissions/access.

For comparison, run the existing YOLO detector against the same individual images
in its separate environment. Compare each verdict against manual labels, including
known cats and empty scenes in daylight and at night. TC41 motion captures are
currently resized to 640 x 360 and annotated with green motion boxes; these can
make small animals harder to judge. Prefer original, unannotated images when
available. This first test does not change the live capture process.

API schema and supported model follow the
[official Decisions guide](https://developers.openai.com/api/docs/guides/decisions).
