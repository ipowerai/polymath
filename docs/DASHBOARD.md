# The web dashboard

A live view of your progress, and a place to study, served by a small web server that
comes with the engine: standard-library Python, no dependencies and no build step.

```
python3 scripts/polymath.py serve --open      # opens http://127.0.0.1:8765/?token=…
```

It prints a sign-in link: open it once and the browser stays signed in for 30 days (the
token is also saved in `web-token` in your data folder). In Claude Code,
`/polymath:progress` offers to start it for you.

The server reads the same files the tutor writes. While you study, the page checks for
changes every few seconds and redraws when an answer is recorded. Stop it with Ctrl+C.

## What it shows

- **Overview.** Days to your next exam, and a card for each goal: predicted score
  against the pass mark, the change this week, which way it is heading, what is due, and
  a button to study it. Below: your latest answers and a calendar of study days. While a
  session is open, a banner shows it live.
- **A goal's Progress tab**:
  - the predicted score, with the pass mark and the booking line (pass mark + 7);
  - **How it's trending**: a one-line outlook, four week-over-week figures (predicted
    score, share of answers right, concepts mastered, answers) with 14-day trend lines,
    and time used against progress made;
  - **Ready to book?**: the three booking checks, and what is left for each;
  - **Up next**: what the next session will ask;
  - **Readiness over time**: your predicted score since you started, with a dotted line
    showing where the last week's pace leads by the exam date;
  - **Right answers**: each study day and the 7-day average, against the pass mark;
  - **Concepts mastered**: mastered and started over time, against your current pace and
    the pace the exam date needs;
  - mastery by exam area, reviews coming due, and mock exams.
- **Concepts, Mistakes, Plan & notes and Settings** tabs for each goal.
- **A concept page**: mastery, knowledge and memory, prerequisites and what builds on
  it, the wrong beliefs seen, its mistakes, and every answer.
- **Mistakes, Activity and You**: the mistake bank, a year of study days, sessions (in
  Claude Code or in the dashboard) and every answer, then confidence calibration,
  accuracy by difficulty and question type, exam technique, and your learner notes.
- **Study and Coach**: question-first sessions, quizzes, mock exams and a study coach,
  all in the browser (below).

Every chart has a **Table** button that shows the same numbers as a table, and every
mark has a tooltip on hover or keyboard focus.

How the trends and the projection are worked out is in
[HOW-IT-WORKS.md](HOW-IT-WORKS.md#trends-the-web-dashboard).

### What you can change from it

Everything a study session records, plus the exam date, a goal's status (active,
paused, done), daily minutes, study settings (questions per block, session length, the
daily cap), mock exam scores (for a practice exam taken elsewhere), notes, and voiding an
answer whose question or answer key was wrong. Each goes through the same engine
functions the tutor uses, and nothing else can be written. Start with `--read-only` to
turn every change off.

## Study in the browser

The **Study** page runs the same method as `/polymath:learn` in Claude Code, and records
into the same files, so you can switch between the two from one session to the next.

| Mode | What it does |
| --- | --- |
| **Learn** | Question-first: open mistakes are re-asked one at a time, then new, review and practice questions come in blocks (3 by default). |
| **Review** | Mistakes first, then what you are about to forget. |
| **Quiz** | 5, 10 or 20 questions on an area, on your mistakes only, or on one concept (from its page). |
| **Mini mock / Full mock** | 20 questions, or the full exam length (up to 200), timed and weighted like the exam. |
| **Coach** | A conversation about your plan and pace, with your real numbers in view. |

### How a session runs

1. **You answer first**: pick an option and say how sure you are (1 to 5). Nothing is
   taught before you try.
2. **A confident miss** (4 or 5) first asks *what made you pick that?*: the belief
   behind the option, a mix-up, a misread, or something else in your words. Your answer
   decides what kind of miss is recorded, and so what is taught. Left unanswered, the
   miss is recorded as it is when you move on or the session closes, dated when you
   answered it.
3. **The teaching starts from your wrong answer**: the belief that leads people to it,
   why it fails, and the rule. A trap or a misread is treated as exam technique, not as
   a knowledge gap. Every miss becomes a mistake that comes back on later days until you
   get it right a day or more after missing it.
4. **A follow-up** asks the same rule in a new case, but only after a separate check has
   answered both questions without the key and agreed with it. Right on the follow-up
   marks the mistake as fixing.
5. **Two misses in a row** on a concept make the next block check what it builds on. A
   quiz keeps to its own questions, so it names what to check outside it instead.
6. **Before the session ends**, it re-checks what you fixed today with one new question
   each. The summary then shows what moved, what was logged, and when it comes back.
7. **When no checked follow-up exists** (the check found a problem with it, or the miss
   sent you to the foundations first), the concept is asked again later in the session.
8. **Think an answer key is wrong?** Dispute it. A separate check judges it; if it
   agrees, the answer leaves your record, and you can void it anyway if it doesn't.
   Disputing a question also voids its follow-up; a follow-up can be disputed on its own.
   While its check runs, ending the session waits for it. A check that can't run, or is
   cut off by a restart, leaves the answer as it was, ready to dispute again; one still
   running after 10 minutes is given up, and its late verdict is dropped.
9. **Voided an answer on its concept page, or in Claude Code?** The session follows at
   once: the card says it is voided, a follow-up or closing re-check for the mistake it
   opened isn't asked, and an open mock review is scored again. An `undo` of that void in
   Claude Code brings the answer back here too, with its follow-up and re-check. A void
   the dashboard made itself (a dispute) stays: undo never takes those back.

Keys on a session page: <kbd>A</kbd>–<kbd>F</kbd> choose, <kbd>1</kbd>–<kbd>5</kbd> how
sure, <kbd>Enter</kbd> check, <kbd>N</kbd> next questions. In a mock exam,
<kbd>←</kbd> <kbd>→</kbd> move and <kbd>M</kbd> marks (flags) a question.

### Mock exams

- The timer starts when you press Start and the server keeps the time: closing the page
  doesn't stop it, and an exam still running two minutes after time is up is submitted
  with your saved answers.
- You can start once five questions are written; the rest arrive while you work. If some
  can't be written, the page says why: try them again, or start with the ones that were.
- Choose-two questions score only when both choices are right, as on the exam.
- Say how sure you are on each answer: it is needed to submit (until time is up), and the
  review shows where you were confidently wrong.
- In the review, dispute any question you think is wrong: if a separate check agrees,
  it leaves the score. The score is logged when you finish the review, or two hours
  after submitting, dated when you sat it. Only full-length mocks count toward the
  booking checks; on an exam longer than 200 questions, the dashboard's 200-question
  Full mock counts as full. When the exam's length isn't known but its time is, a Full
  mock gets the exam's own time.

### Who writes the questions

Choose it on the **AI model** page (see [AI model](#ai-model)). Started with `--tutor`, the
server uses that writer for the whole run instead, and the page can't change it:

| `--tutor` | Writer |
| --- | --- |
| `auto` (default) | Claude Code (`claude -p`) on your subscription, if it is installed. Never falls back to the API. |
| `claude` | The same, by name. |
| `api` | The Anthropic API, billed to `ANTHROPIC_API_KEY` per token. |
| `fake` | Built-in practice questions, with no AI (the default with `--demo`). |
| `off` | No study in the browser. |

`--model` (default `opus`, or `POLYMATH_MODEL`) and `--effort` (default `low`) choose the
model and how hard it thinks. With Claude Code, a block of three questions takes about
20 to 50 seconds to write; the next block is written while you answer the current one.

Claude Code runs locked down for every call: no tools, no MCP servers, no project files
or settings (it runs in an empty folder with a minimal environment), and no saved
session. Its start-up report is checked before a result is used: a call that starts with
tools, MCP servers or an API key is refused.

### What is sent to Claude

For each question: the concept, its objective and exam area, the difficulty and
format, the wrong beliefs already recorded on that concept, the summaries of recent
questions (so none repeats), rules found wrong in earlier disputes (so they are not
asserted again), and a few lines of your learner profile (background, how you learn
best, weak spots). The coach also gets your progress numbers, plan, notes and the last
journal entries. Your sign-in token and other files are never sent. Everything sent is
marked as data, not instructions.

What comes back is checked before you see it: the format, the number of options and
right answers, banned options such as "all of the above", and whether a follow-up really
tests the same rule. The options are shuffled, and the answer key reaches the page only
after you answer.

### Usage and the daily cap

Every call is logged in `web-usage.jsonl` in your data folder: when, what for, how long,
and the cost at API prices. On a Claude subscription nothing is billed per call, but the
calls count against your plan's usage limits. Study stops writing new questions for the
day once it reaches a cap: $10 at API prices or 400 calls by default, changed under
Settings on the Study page (`preferences.web_daily_usd` and `web_daily_calls`). Each call
running holds what it is likely to cost against the dollar cap (the dearest question of
its kind today, times the questions it writes: five for a mock exam's calls), so calls
started together can pass the cap only by a little.

### Sessions

- **One open session per goal.** Starting the same mode again resumes it; another mode
  closes it first. A mock exam that is still running has to be finished first.
- **Restarts are safe.** Open sessions are saved in `web-sessions/` and resume when the
  server starts again; questions being written are written again.
- **A session waits for you.** Leave Study and the **Study** tab brings you back to the same
  screen: the questions, what you chose but hadn't checked, what you had open, and your place
  on the page, even after a reload. If you had answered the whole block, it asks whether to
  keep reviewing or move to the next questions. Time away doesn't count as time on a question.
  **Study home** is on the session page.
- **A session left for over a week closes**, with its summary saved, dated when you stopped
  (not when the computer woke up and noticed).
- **Records match Claude Code.** Each answer, the session start and end, and a journal
  entry ("in the dashboard") are recorded as the tutor records them. Undo in Claude Code
  leaves the dashboard's records alone (it steps past them only to the tutor's own study
  records from the last 12 hours, and stops at an answer already voided here); void a
  dashboard answer from its concept page instead.
- **Nothing is half-recorded.** If an answer can't be recorded (a full disk, say), the
  question stays unanswered and sending it again works; an answer is never recorded
  twice. Once an answer, a session's end or a mock is in the log, it stays recorded even
  if saving the session fails just after: the page keeps showing it, the dashboard saves
  it again within a minute, and a session closed that way is never reopened, and its
  track note is added once. A mock that the clock submits after the computer slept, or
  after a restart, is dated when time ran out, whatever was recorded in between (the
  learner model replays every record in the order things happened), and you still get
  two hours to review it once it has been graded.
- **One dashboard per data folder** runs study. A second one on the same folder serves
  the dashboard without study.
- **Docker:** file locks may not reach across a folder shared with the host. Don't study
  in a container while Claude Code writes the same folder on the host.

## Preview it with demo data

```
python3 scripts/polymath.py serve --demo
```

This builds a simulated learner (twelve days of PD1 study) in a temporary folder, serves
it with the built-in practice questions, and deletes it when you stop the server. Your
own data is not touched.

## A snapshot file instead

```
python3 scripts/polymath.py dashboard --open
```

This writes `~/.polymath/dashboard.html`: the same page with the data inside it, as one
file that opens without a server. It does not update and can't change anything or run
study.

## Run it somewhere else

The server reads the data folder on the machine it runs on (`~/.polymath`, or
`POLYMATH_HOME`). Pick the option that matches where you study.

| You want | Do this |
| --- | --- |
| The dashboard on this computer | `serve`. It listens on 127.0.0.1 only; open the sign-in link it prints once. |
| To see it from another device, without exposing anything | An SSH tunnel or Tailscale (below). Nothing listens beyond this machine. |
| To reach it on your home network | `serve --host 0.0.0.0`, then sign in with the printed token. |
| A server you also study on (a dev box, a VPS) | `serve --host 0.0.0.0` there, ideally with TLS or behind a reverse proxy. |
| A container | The Docker image below. |

Study in the browser needs a question writer on the machine that runs the server:
Claude Code logged in there, or `--tutor api` with an API key.

### SSH tunnel (the simplest safe remote view)

On the machine you are looking from:

```
ssh -N -L 8765:127.0.0.1:8765 you@study-machine
```

Then open the sign-in link the server printed (`http://127.0.0.1:8765/?token=…`), or
open http://127.0.0.1:8765 and paste the token from `web-token`. The server still
listens only on localhost.

### Tailscale

On the study machine:

```
python3 scripts/polymath.py serve --allow-host study-machine.your-tailnet.ts.net
tailscale serve --bg 8765
```

Then open `https://study-machine.your-tailnet.ts.net/?token=…` (the token from the
printed link, or from `web-token`) from any device on your tailnet. Only your tailnet can
reach it, and only with the token. `--allow-host` lets the server answer to that host name.
The `tailscale serve` syntax has changed between Tailscale versions; check
`tailscale serve --help` if it is rejected.

### On your network, or on a server

```
python3 scripts/polymath.py serve --host 0.0.0.0
```

A sign-in token guards the dashboard everywhere: on localhost too, because other accounts
and programs on the machine can reach 127.0.0.1, and study spends your Claude usage. Only
a `--read-only` dashboard, or the demo, opens on localhost without one.

- **The token.** Polymath generates one on first use and saves it in `web-token` in your
  data folder. The file is readable only by you, and listed in the folder's `.gitignore`.
  Saving it again from an editor that adds a byte-order mark, or as UTF-16, leaves the
  token as it was.
- **Signing in.** The server prints a sign-in link containing the token. Open it once,
  or paste the token into the sign-in page. The browser then holds a random sign-in id,
  never the token, in a cookie, and the page keeps a key that goes with it in its own
  storage. Each sign-in lasts 30 days (the server checks), ends when you sign out, and
  works only on the dashboard that issued it. `web-auth.json` keeps a hash of each one, so
  sign-ins survive a restart. Clearing the site's data in the browser signs it out: open
  the link or paste the token again.
- **Choosing your own.** Set `POLYMATH_WEB_TOKEN`, or pass `--token-file <file>` (`-` reads
  it from stdin). `--token <value>` works too, but other accounts on the machine can see
  a command line, so the server warns about it.
- **Changing it.** `--rotate-token` makes a new token and signs every browser out.
  Stop the dashboard first if it runs on the same port. A dashboard running elsewhere
  that uses the saved token switches to the new one as soon as `web-token` changes.

The connection is plain HTTP unless you add TLS. On any network you don't fully trust,
use `--tls-cert cert.pem --tls-key key.pem`, a tunnel, or a reverse proxy that
terminates HTTPS. For example, with Caddy:

```
# Caddyfile
polymath.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

```
python3 scripts/polymath.py serve --allow-host polymath.example.com           # the saved token
openssl rand -base64 24 | python3 scripts/polymath.py serve --token-file - --allow-host polymath.example.com
```

With nginx, forward the name the browser used (`proxy_set_header Host $http_host;`, or
`X-Forwarded-Host`), or at least start the dashboard with `--allow-host <that name>`, as
`<name>:<port>` when the proxy listens on a port other than 80 or 443: by default nginx
passes its own upstream address as the host.

Behind a public proxy, keep the token (the saved one, or your own), and never put a
`--read-only` dashboard there: on localhost it has no sign-in, and the proxy would hand
that open access to the internet.

### Docker

```
docker build -t polymath .
docker run -d --name polymath -p 8765:8765 \
  -v ~/.polymath:/data --user "$(id -u):$(id -g)" \
  -e POLYMATH_WEB_TOKEN="choose-a-long-random-token" -e TZ=America/Los_Angeles \
  polymath
```

- **The data folder** is mounted at `/data`.
- **`--user`** lets the container read your files, which are private to you.
- **`TZ`** makes study days and streaks follow your local midnight, not UTC.
- **Without `POLYMATH_WEB_TOKEN`**, the token is generated and printed in
  `docker logs polymath`.
- **`docker stop`** shuts the server down cleanly.
- **Study** in the container needs a question writer inside it. The image has no Claude
  Code: build it with `--build-arg WITH_API=1` and run it with
  `-e POLYMATH_TUTOR=api -e ANTHROPIC_API_KEY=<your key>` (billed per token), or study on
  the host and use the container only to view.

### Studying on one machine, viewing on another

The data is plain files with an append-only event log. To show it on a machine you don't
study on:

1. Sync the folder one way, from where you study to where you serve, with rsync,
   Syncthing or a private git repository.
2. Run the server there with `--read-only`.

Don't let two machines write to the same folder at once.

## Security, in short

- **A token is required**, on localhost too, unless the dashboard is read-only or the
  demo. A browser carries a sign-in id in an HttpOnly cookie named for the dashboard's
  port, and the page sends a key that goes with it from its own storage. Browsers hand a
  host's cookies to every port on it, so a program on another port of this machine can
  be given the cookie; it never gets the key, which only the dashboard's own page can
  read, so the cookie alone opens nothing, and a key planted in the page's address is
  kept only if the server accepts it. Cookies planted by another port can't hide yours;
  a flood of large ones can make the browser's requests too big to send, and the page
  then says to clear this site's cookies. Scripts send `Authorization: Bearer <token>`.
- **Without a token** (a read-only dashboard or the demo, on localhost), the server
  answers only to local host names, so a web page that points its own domain at
  127.0.0.1 (DNS rebinding) is refused. Add names with `--allow-host`.
- **Changes** need a header that only the page itself sends, from the same origin
  (checked with the browser's `Sec-Fetch-Site`, and, where a browser doesn't send it,
  with `Origin`, which must name the host and port the request came to, or a name given
  with `--allow-host` behind a proxy that rewrites the host). Other sites, including
  other ports on this machine or on the dashboard's host, can't trigger them or sign you
  in or out.
- **Sign-in links** for a host name (`--host localhost`, say) use the address the server
  actually listens on, so a program listening at another address for that name never
  receives the token. With TLS they keep the name the certificate holds.
- **Your files** are private: the data folder is readable only by you (0700, files 0600),
  and any command tightens a folder made by an older version.
- **The server itself** closes idle connections after a minute, and with TLS each
  handshake runs on its own with a 10-second limit, so one stalled client can't freeze
  the dashboard.
- **A second start** on a port already in use checks that the dashboard there is really
  the one for this folder before it opens a sign-in link to it: it asks a random challenge
  that only a process able to read `web-ping-key` (private, random) can answer.
- **The page** loads nothing from the internet. A strict Content-Security-Policy allows
  only its own script, styles and data. All text, including everything Claude writes, is
  inserted as text, never as HTML, and only `https:`, `http:` and `mailto:` links are made.
- **Claude Code** runs with no tools and no MCP servers, in an empty folder, and its
  start-up is checked on every call (see [Who writes the questions](#who-writes-the-questions)).
- **Never** expose the server without a token, or over plain HTTP on a network you don't
  trust.

## For scripts

Every page is drawn from a JSON API. Pass `Authorization: Bearer <token>` when a token
is in use. Changes are `POST` requests with a JSON body and the header `X-Polymath: 1`.

| Endpoint | Returns |
| --- | --- |
| `GET /api/state` | Everything the page shows: goals, history, trends, mistakes, sessions, activity. |
| `GET /api/version` | A value that changes whenever the data does. Poll it cheaply. |
| `GET /api/concept/<id>` | One concept, with every answer on it. |
| `GET /api/attempts?offset=0&limit=50&track=<slug>` | Answers, newest first. |
| `GET /api/study` | Whether study is available, the writer, sign-in, today's usage, and open sessions. |
| `GET /api/study/<id>` | One session: the current block, results, totals and summary. |
| `POST /api/study/start` | `{"mode": "learn"\|"review"\|"quiz"\|"mock", "track": "<slug>", "count": …, "scope": …}` |
| `POST /api/study/<id>/<action>` | `answer`, `reason`, `followup`, `next`, `retry`, `keep`, `dispute` (`which`: `question` or `twin`), `end`, `mock/begin`, `mock/save`, `mock/submit`, `mock/finish`. |
| `GET` / `POST /api/coach` | The coach conversation; `{"message": "…"}` sends one. |
| `POST /api/preferences` | `session_minutes`, `block_size`, `desired_retention`, `start_level`, `web_daily_usd`, `web_daily_calls`. |
| `GET /api/ping` | `{"app": "polymath", "version": …}`, with no sign-in needed. With `?challenge=<16-128 hex digits>` it adds a `proof`: HMAC-SHA256 keyed by `web-ping-key` over `polymath-ping-v1:<address:port reached>:<challenge>`. |

Errors are `{"error": "…", "code": "…"}`; the code (such as `daily_cap`, `usage_limit`,
`signed_out` or `stale`) says what went wrong.

## AI model

The **AI model** tab chooses which Claude model writes your questions. Your progress stays when you
change it.

| Kind | Options | Where your study data goes |
| --- | --- | --- |
| Claude subscription | Opus, Sonnet or Haiku through Claude Code | To Anthropic, on your plan |
| API key | Any Claude model, if the optional `anthropic` Python package is installed | To Anthropic, billed to your key |

This release offers Claude only. Support for other models stays in the code, switched off by
`ENABLED` in `scripts/polymath_models.py`, until each one has been measured. The model benchmark
in `scripts/polymath_bench.py` is off for the same reason.

- **A choice is checked before it is used.** If it fails, nothing changes.
- **If the model stops,** a notice appears on every page saying what is wrong and what to do. Your
  answers stay safe, and questions that failed are written again when it returns. A saved choice
  that can't run is reported, never swapped for another.
- **Keys** are kept in `models.json` in your data folder, readable by your account only and left
  out of git. They are never sent to the page or logged.
- **Web lookups need Claude Code.** Lessons, goal research and company or interviewer research
  say so when you use an API key. Practice, reviews, mocks and the coach work either way.

## Goals

**Goals → Add a goal.** Say what you want in your own words ("I want to achieve the Salesforce CTA
certification"). Polymath then:

1. works out what you mean, asking only if it is truly ambiguous;
2. looks up the current official requirements and their whole dependency chain;
3. asks up to five questions that research makes worth asking (what you already hold, your experience,
   your time, any deadline);
4. shows your gaps and a roadmap with dates. You can change it by talking to it, or by editing the minutes
   a day and the target date.

Each step opens when what it depends on is done. Starting one uses the track you already study, a bundled
syllabus, or a new one built from the official guide (marked unverified until you check it).

Only the research, syllabus and lesson calls can reach the web, and they can do nothing else. They are sent
the public subject alone, with your name, emails and phone numbers taken out; the calls that see your
background have no tools. Claude never writes a date: the schedule is computed from hours, dependencies
and your minutes a day.

## Lessons inline

Once a question is answered, its key terms are underlined. Click one to read a lesson on it right there,
and click it again (or **Hide**) to close it. You can also select any words on an answered question.
Nothing is linked before you answer, because a lesson could give the answer away.

A lesson is written once from the official documentation, kept in `lessons/`, and opens instantly after
that. It lists the pages it came from; one with no source is refused.

## Active time

Time counts only between your own actions (a click, a key, a scroll, the pointer moving) while the window
is on screen and in focus. A pause over three minutes, a hidden tab or another window adds nothing, and two
open tabs can't count the same time twice. It is kept per day, track and session in `active-time.json`.

## Interview prep

**Goals → Prepare for an interview.** Add the job, its description, your resume (paste it, or upload a PDF,
Word, RTF, text or Markdown file) and, if you know them, who will interview you. Polymath then:

1. researches the company, the role and each interviewer's public professional background;
2. compares your resume with the job: strengths, gaps, what they'll probe, and real stories to prepare;
3. writes 25 to 35 likely questions, most likely first, each with the evidence for it;
4. has you answer first, then scores it, shows a stronger version built only from your own experience, and
   asks the follow-up an interviewer would.

What goes where:

- The web calls get only the company, role and level, the job and company links, and each interviewer's name,
  title and profile link.
- Your resume, the job description and your notes go only to calls with no tools.
- About an interviewer, only professional facts are looked for. If no source ties that name to this company,
  nothing about them is kept or used. If a profile can't be read, paste their bio.

These are predictions, not known questions. Changing the details researches only what changed and keeps your
practice.

