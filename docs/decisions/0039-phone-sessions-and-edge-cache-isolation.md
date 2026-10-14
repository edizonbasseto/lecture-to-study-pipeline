# 0039. Phone sessions, per-person revocation and edge-cache isolation

## Status

Accepted. Amends ADR 0029 (study site scope, video delivery and access control).
**Addendum (2026-09-17):** the two "Negative, outstanding" consequences below
about a lost destination and no sign-out are resolved -- the denial redirect to
`/entrar` now carries the originally requested path as a validated `next` query
parameter, `POST /entrar` returns there on success, and a new `GET /sair` route
clears the session cookie. There is still no way to revoke a single device
short of rotating the person's password; only the "lost destination" and
"no sign-out" gaps are closed.

## Context

ADR 0029 settled access as an email allowlist with a password per person, checked
on every request through Basic Auth. That held for scripts and for the deploy
tool's health check, but not for the audience the site exists for: the second
person reads on an iPhone, most often through a link opened inside WhatsApp, and
an in-app browser never surfaces the Basic Auth prompt. The only thing visible
there was the 401 body -- a dead end.

A login form with a signed session cookie was added to close that gap and shipped
to production before it had been reviewed. The review that followed found the
shipped design sound in its cryptography and wrong in four ways that matter:

1. **Nothing forbade shared caching.** Responses carried no `Cache-Control`, and
   the site sits behind a CDN that caches by file extension without regard for
   the request's cookie. A PDF fetched once by an authorized person could be
   served from the edge to anyone holding the URL, with the app never running.

2. **A signature was treated as access.** The cookie proved a token had been
   signed, but its address was never checked against the allowlist again, so
   removing a line did not revoke the session it had issued. The allowlist file
   was also bind-mounted into the container as a *file*, which pins its inode:
   any atomic rewrite on the host (what every editor does) left the running
   container reading the pre-rotation contents until someone restarted it.
   ADR 0029 promises revocation "with no redeploy and no restart"; this broke it
   twice over.

3. **Revocation was all-or-nothing.** The signing key was derived from the bytes
   of the whole allowlist, so adding one person invalidated everybody's session.
   ADR 0029 requires "revocation of one person without disturbing another".

4. **The form was a free amplifier.** `POST /entrar` is reachable without
   credentials and costs one PBKDF2 pass per allowlisted person, run on the event
   loop of a single-worker container. Measured: 18 ms with one person, 513 ms with
   thirty. Without a limit it is both a guessing oracle and a cheap way to take
   the library down.

## Decision

1. **Every authorized response declares `Cache-Control: private, no-store`**, so
   no shared cache may hold content the middleware authorized. The public landing
   page is the one response that stays cacheable, because it is what link
   previews fetch and it carries nothing private.

2. **A session token is signed with a key that mixes a dedicated server secret
   with that one person's stored password hash**, not with the allowlist as a
   whole, and validating a token requires finding its address in the allowlist as
   it reads *now*. Removing a line revokes exactly that person; changing one
   password invalidates exactly that person's sessions; adding somebody disturbs
   nobody. This is what ADR 0029 asked for and what the first implementation did
   not deliver. The server secret is what keeps the password hash a verifier
   rather than a bearer key: the hash exists so that holding it is not the same
   as authenticating, and keying HMAC with it alone would have turned any read of
   the allowlist -- a backup, a volume snapshot, an rsync copy -- into instant,
   silent forgery of a thirty-day cookie for anyone on the list. The secret is
   generated once on the server by the deploy script and never leaves it; a
   missing or too-short secret denies everyone, exactly as a missing allowlist
   does.

3. **The allowlist is mounted as a directory, never as a file**, so a rewrite on
   the host is visible to the running container immediately, and it is synced
   with mode 600 into a directory with mode 700. **The signing secret lives in a
   separate directory**, mounted separately and never synced or copied with the
   allowlist: forging a cookie must require both files, so one backup or one
   rsync must not carry both. The deploy replaces the allowlist before removing
   the older world-readable copy, so a failed transfer never leaves the site
   with no credentials at all.

4. **`site.sessions` never touches the filesystem.** It is handed the credentials
   its caller already read, so a missing or unreadable allowlist fails closed at
   the single place that reads it -- returning 503 -- instead of raising an
   unhandled error on any request that happens to carry a cookie.

5. **Password checking is limited on the gate both doors pass through**, so
   guessing cannot switch from the form to Basic Auth to escape the limit. Two
   distinct limits guard two distinct failures, and neither may be turnable
   against the person the site exists for.

   Password checks for one address are **serialized, and the number waiting on
   it is capped**. Without serialization a growing wait is theatre: a hundred
   requests read the same wait at the same moment, sleep through it together and
   then check in parallel, so guessing costs an attacker one wait rather than a
   hundred (measured before this was fixed: 27.7 guesses a second against a
   single address, none refused). Serializing makes each guess pay the wait the
   guesses before it earned. The in-flight cap is what stops the queue itself
   from becoming the attack, since a sleeping request holds a connection: beyond
   it, guesses are refused at once with 429 rather than parked.

   Repeated failures for one address earn a **growing wait, never a lockout**.
   A lockout is aimed as easily at the rightful owner as at an attacker: anyone
   who knows her address could spend ten wrong guesses every five minutes and
   keep her out of her own library indefinitely, and there is no password reset
   to recover through. A wait that doubles to a cap costs an attacker the same
   throughput while the person who simply mistyped waits seconds and gets in.
   Waits are counted **per address attempted, never per network address**:
   behind the TLS proxy every visitor shares one peer address, so an IP-keyed
   counter is not a per-attacker budget at all.

   Separately, a concurrency limiter caps how many checks run at once and sheds
   load with 429 once the queue behind them is long. Unlike a lockout it cannot
   be aimed at one person: it lasts only as long as the flood.

   The table of waits is bounded, and it is trimmed by dropping the address with
   the **least** evidence against it. Clearing it wholesale, or trimming by
   recency, would both let an attacker name a thousand throwaway addresses to
   erase the wait he had earned against the one address he is actually after.

   The request body of the form is bounded and an oversized body is refused as
   too large rather than counted as a wrong guess -- otherwise refusing to read
   a body would itself become a way to slow somebody down. Every password check
   runs off the event loop. Both limits are in-process, matching the
   single-replica deployment; they are a first barrier, not a distributed quota.

6. **Basic Auth stays** for scripts, the health check and the deploy tool. A
   caller that accepts HTML is redirected to the form; one that does not still
   gets the 401 challenge.

## Alternatives considered

1. **Signing with the stored password hash alone, with no server secret.**
   Rejected after review: it does give per-person revocation, but it promotes a
   verifier to a bearer key, so reading the allowlist becomes enough to forge a
   cookie without cracking anything. Mixing a server secret into the key keeps
   every revocation property and costs one file the deploy script generates
   once.
2. **Server-side session storage with explicit session identifiers.** Rejected
   for now: it buys individual sign-out at the cost of state the site otherwise
   does not have. Revisit if sign-out from a lost phone becomes a real need
   rather than a hypothetical one.
3. **Throttling by network address.** Rejected: the app sees the TLS proxy's
   address for every visitor, so the counter would be one shared bucket and ten
   failures would lock everybody out. Trusting a forwarded-for header instead
   would hand the key to the caller.
4. **A growing wait charged only on failure, with no per-attempt claim.**
   Rejected once measured: requests that arrive together all read the same wait
   and pay it once between them, so the limit exists only against an attacker
   polite enough to wait his turn.
5. **Locking an address out after N failures, or capping how many attempts may
   be in flight for one address.** Both rejected, the second after being built,
   measured and reverted: with three connections held open against one address,
   a correct password from its owner was refused every time. Any limit keyed by
   the address under attack is a lockout with extra steps; the cap had to move
   to a single room shared by everyone, where filling it costs an attacker
   sustained connections and degrades the whole library rather than singling
   out one person.
6. **Dropping Basic Auth entirely once the form existed.** Rejected: the deploy
   tool and the smoke checks have no browser and no cookie jar.
7. **Relying on the CDN's configuration alone to keep private files out of the
   edge cache.** Rejected as the only control: it is not in version control, it
   is not visible from the code, and a console change nobody records would
   silently expose the whole corpus. The header is the control; a cache rule is
   defence in depth on top of it.

## Consequences

- **Positive:** a phone reaches the library through a normal form, and revoking
  one person is again a single line edit that takes effect on the next request.
- **Positive:** authorized content can no longer be parked in a shared cache.
- **Negative, accepted:** changing somebody's password signs that person out of
  every device, which is the intended meaning of a password change.
- **Negative, accepted:** there is still no way to revoke one device without
  revoking the person -- signing out only clears the browser's own cookie
  (see Addendum above). A lost phone is handled by rotating that person's
  password.
- **Negative, accepted:** a flood of password checks is shed with 429 for the
  duration of the flood. Somebody signing in at that exact moment retries.
- **Negative, accepted:** somebody guessing at a known address makes that
  address slower to sign in with -- up to the cap -- for as long as the guessing
  lasts. Slower, never refused: a correct password always gets through.
- **Resolved (see Addendum above):** a link shared into WhatsApp used to lose
  its destination -- the redirect to the form did not carry the requested
  path, and signing in always landed on the library index.
- **Positive:** a brute-force attempt leaves a trace in the container's own
  logs that outlives the in-process table.
- **Deployment consideration:** rolling back to an image older than this
  decision restores the edge-cache leak it fixes, because that image emits no
  cache directive. A rollback across this boundary must be followed by purging
  the CDN cache.
- **Deployment consideration:** the signing secret and the allowlist should sit
  in separate directories with separate mounts. When both live under one data
  root, a backup scoped to that root carries both, and the separation only holds
  for copies scoped below it.
- **Deployment consideration:** the `Cache-Control: private` header is the
  control; whether a given CDN honours it is a property of that CDN's
  configuration. Anyone hosting the site behind an edge cache should confirm it
  once, for example by checking that the CDN's cache-status header on an
  authorized PDF reports a bypassed or dynamic response rather than a hit.
- **Deployment consideration:** the session cookie and any Basic credential
  travel on every hop between the browser and the application. When a CDN or
  reverse proxy terminates TLS, the leg between it and the origin should also be
  TLS with the origin certificate validated (for a CDN, its strict-origin mode).
  Otherwise a credential on that leg is exposed to anyone in the path, and a
  captured cookie is valid for its full lifetime with no way to revoke one
  device short of rotating that person's password. This is hosting
  configuration, outside the code.
