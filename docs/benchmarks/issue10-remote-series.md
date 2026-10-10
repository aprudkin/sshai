# Issue 10: remote-series execution and preparation

**Issue:** [sshai#10](https://github.com/aprudkin/sshai/issues/10).
The [methodology amendment](issue10-methodology-amendment.md) remains authoritative.
The [completed local report](issue10-local-measured-results.md) is a distinct retained
population, not qualification of either remote platform.

## Linux measurement status

The four original Linux pilot sessions and all 36 original measured diagnostic sessions
completed once on 2026-10-06, with no replacement, retry or schedule change. Independent
technical review qualified all measured finals and cumulative usage. Recorded conformance
is separately failed for slot 18 and unknown for slots 24 and 29; only 15/18 pairs have
fully supported conformance. The complete population remains retained.

Six original same-task measurement assessment contexts completed once in L01–L06 order
on 2026-10-09, with both arms and all three repetitions per task. Fresh independent technical
review qualified their finals separately; all response publications passed objective
schema/citation-coordinate validation. The retained model grades yield 16/18 baseline and
14/18 sshai rubric-successful answers, despite correct diagnoses in all answers. The prescribed
no-observed-degradation criterion fails. These model judgments do not establish human truth.
The full conforming estimate remains unavailable. See the
[Linux results report](issue10-linux-measured-results.md) for complete descriptive results.

Study consumption is now 80/120 diagnostic slots and 15/24 assessor contexts. Assessor
usage is separate from diagnostic usage; this is caller accounting, not provider billing.

## Windows qualification status

Windows remains unqualified. The user confirmed the existing target is a working server
and limited investigation to its separate synthetic test directory; a new server is not
an automatic prerequisite. An independently reviewed controller comparison created the
PowerShell image suspended and immediately terminated it without resumption or input.
The one-process job became empty; the owned comparison fixture was removed with guarded
cleanup. This does not establish the cause of the earlier LPAC startup error 2.

On 2026-10-09, the user explicitly approved one temporary task-named AppContainer profile
for the existing SSH user, bounded no-model synthetic checks and guarded removal after
terminal processes. This one-time scope includes only the new profile's per-user
folders/ACL/registry state outside the test directory. It does not permit new OS users,
global/runtime ACL changes, packages, service/firewall/host-key changes, production-data
access, model requests or further profiles. New implementation requires independent
review before use. This recorded decision is not a reusable permission grant or Windows
study launch approval.

That allowance has now been consumed by one independently reviewed profile-first
no-model probe: exactly one `CreateAppContainerProfile` and one `DeleteAppContainerProfile`
call both returned `S_OK`. The exact profile association was qualified, and profile
mapping/folder absence after deletion was verified. Guarded cleanup removed the owned
synthetic fixture while preserving its parent, consumption marker and earlier diagnostics.

The LPAC `CreateProcessW` call returned Win32 error 203
([`ERROR_ENVVAR_NOT_FOUND`](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-))
before token validation, resumption or any runtime canary. The missing environment
option/internal lookup is unknown; this result does not establish the earlier error-2
cause. Listener/native handles were closed without reported cleanup errors. These are
source/API-bound receipts, not independent OS tracing. No model or study slot was consumed.
Windows runtime/data/network/descendant qualification remains unavailable. Source-only
environment-contract investigation can continue; a further live profile probe requires
a new explicit allowance, not reuse of this consumed grant.

The user subsequently allowed one additional no-model profile test, changing only the
child environment by adding `LOCALAPPDATA` from the current user's Windows known-folder
API. Path qualification and broker-field presence/equality checks passed. With that
nine-entry block, `CreateProcessW` created a suspended process and job assignment
succeeded. Token validation then returned Win32 error 87 (`ERROR_INVALID_PARAMETER`);
the exact failed query and actual token qualification remain unknown. The process was
never resumed, so no runtime canary ran. It was terminated and the job emptied; listener
and native resources were cleaned without reported errors. The additional profile was
created/deleted once with `S_OK`, its absence verified and its owned fixture removed.

At that checkpoint, both individual allowances were consumed: two profile creations
and two deletions in total, with no model/study slots. This preparation advanced past error 203, but fresh
profile/SID/nonce/CWD and no same-profile counterfactual preclude claims of `LOCALAPPDATA`
necessity, sufficiency or historical causality. Requested LPAC attributes are not actual
token proof. Windows remains unqualified; token-API support investigation is separate
from any further live profile allowance.

A subsequent independently reviewed read-only query of the controller's own token
rejected the four-byte class-46 request with error 87. Its payload was not interpreted;
this is compatibility evidence for that request/context, not proof of the historical
failed query or child identity. No new profile or test child was created.

The user then approved one further profile with a never-resumed regular-AppContainer
control and an LPAC candidate. Both actual tokens passed documented AppContainer,
zero-capability, exact-profile-SID and low-integrity checks. An in-memory `AccessCheck`
comparison granted exactly mask 3 to the control and mask 2 to the candidate, with zero
privileges used. The control was terminal before candidate creation; the candidate had
an independent job. This confirms effective disregard of `ALL_APPLICATION_PACKAGES`
grants in that controlled setup, not universal LPAC identity or arbitrary-code confinement.

Only after those gates did the candidate resume. It exited with `0xC0000142`, capturing
zero bytes and producing none of the runtime canary markers. The candidate job accounted
for two processes, but the second process's origin is unknown; without its marker it is
not a proven canary descendant. Runtime/data/network/stdio/descendant qualification
remains unavailable. The exit status alone identifies neither a DLL nor an ACL cause.

Both jobs were terminal/empty and native resources/listener were cleaned without reported
errors. The new profile was created/deleted once with `S_OK`, its absence verified, and
its six-entry owned fixture removed while preserving the parent and earlier evidence.
At that checkpoint, three individual profile allowances were consumed: three creations
and three deletions, with no retry or model/study slots. Read-only startup diagnosis could
continue; another live profile action required a new explicit allowance. These remain source/API-bound receipts,
not independent OS tracing or Windows study readiness.

Read-only inspection subsequently verified that the installed native PowerShell host
statically imports USER32/SHELL32. Exact process/time event queries did not identify the
faulting module or second job member. A bounded inventory found a service-session
windowstation and `Default` desktop with no selected Everyone/AAP/ARAP ACEs. This is
current controller context, not an effective-access or historical-child attribution.

Under a fourth separate one-profile allowance, two reviewed minimal native images were
compared: KERNEL32-only entry logic and the same logic with an added static USER32 import,
never invoked by the entry code. Each image had its own never-resumed regular control and
gated LPAC candidate. All four actual-token checks and functional control-3/candidate-2
checks passed. The KERNEL32-only candidate emitted its exact 41-byte startup nonce and
exited 0; the USER32-import candidate emitted nothing and exited `0xC0000142`.

This narrows the added USER32/dependency initialization boundary in that new controlled
context. It does not identify a precise DLL operation, prove a desktop-ACL cause, or
retrospectively attribute the earlier PowerShell failure. Both candidate jobs counted
two processes; the extra origins remain unknown, even for the successful no-child-code
image. No trusted descendant or full PowerShell/runtime/study readiness follows.

All four phases were terminal with known resource cleanup. The new profile was
created/deleted once with `S_OK`, its absence verified, and its eight-entry fixture
removed while preserving the parent and earlier evidence. At that checkpoint, four
individual profile allowances were consumed: four creations/four deletions, without
retry or model/study slots. No existing ACL, environment, desktop/windowstation or runtime
was changed.

A fifth separate allowance covered one new profile and a temporary private noninteractive
windowstation/desktop, exact-package rights and low labels, with one ordinary native
helper and at most two USER32 qualification processes. The reviewed preparation's
lifetime-job-count cleanup defect was corrected and independently re-reviewed before
its single invocation; synthetic checks did not establish live Windows behavior.

That invocation created the profile once with `S_OK` and launched one helper, then stopped
at helper readiness with `InvalidOperationException`. Neither qualification process was
created or resumed; no actual-token/functional comparison or USER32 startup result was
obtained. READY/CLOSED proofs, the precise failure cause and actual private-object
creation/restoration/closure state are unavailable. The last traced native API succeeded;
its trace does not identify the failing operation.

The receipt reports the helper primary signaled/job empty and six successful handle
closures, but full helper/object cleanup remains unknown. Profile deletion and fixture
cleanup were withheld, preserving the new profile, fixture and consumed journal without
retry. At that checkpoint, all five profile allowances were consumed: five creations/four
deletions, with the fifth profile retained pending qualified recovery. No model/study
slots were consumed; Windows runtime/study readiness remained unavailable.

A separately authorized storage-only recovery accepted persistent GUI uncertainty. Its
cleanup code and deadline correction passed independent review before one invocation.
The first fifth-profile SDK Delete returned `S_OK`: cumulative actual Create/Delete
attempts are now five/five. Exact mapping absence and preflight-bound physical-folder
absence were observed, but the remaining ancestry/SDK-association sequence stopped with
`FileNotFoundException`. Full storage verification was not established; the precise
failing subquery/path is unrecorded, and no repeat Delete is authorized.

At that checkpoint, no fixture removal had been attempted. The fixture and journal
remained, with final parent/claim preservation readback not reached. These were never
deletion targets, but their final preservation was not independently verified. GUI object
existence, session, restoration and closure remained unknown; neither complete rollback
nor Windows study readiness was claimed. No profile/helper/qualification/startup or
model/study run was added.

A new, separately authorized fixture-only invocation accepted a narrower evidence basis:
the earlier qualified-folder absence observation plus renewed exact owner/SID, mapping
and current SDK-association queries, without the unavailable historical folder path.
The standalone cleanup code passed 149 synthetic tests twice, eight behavioral mutation
checks and fresh independent review before its sole invocation. It has no SDK Create/Delete
binding and does not reuse the consumed recovery entry point.

That invocation verified removal of four files, two empty subdirectories and the empty
fixture root. Final readbacks verified preservation of the parent marker, consumed claim
and old recovery journal. SDK Create/Delete calls were zero/zero; cumulative actual
attempts remain five/five. The new fixture-only allowance is consumed, without retry.

Current mapping absence was observed, but the SDK folder query returned `0x80070002`
with no usable path. Current physical-path and ancestry checks therefore remain
unavailable, not passed. Fixture absence is verified only on the accepted limited
historical/current basis. Historical full storage verification remains incomplete;
GUI object existence, session, restoration and closure remain unknown. Neither full
rollback nor Windows runtime/study readiness follows, and no model/study slot was added.

## Preparation checkpoint before the Linux pilot

The following records the earlier no-model preparation, not the current slot consumption.

The user resumed the remaining issue with new task-wide advance autonomous authorization.
Necessary implementation, verification, independent review and repository delivery are
covered; ordinary technical choices are coordinator-selected, not individually selected
by the user. This does not waive technical readiness, private evidence handling, one-shot
allocation or the explicit prohibitions on production-data access, repairs, system-package
installation, service/firewall changes and SSH host-key-policy changes.

Fresh bounded runtime-only checks reached both previously supplied targets. A dedicated
synthetic Linux preparation subsequently passed **50 no-model checks** on the current
boundary: 25 remote view/privilege/identity checks, 21 local access/network checks and
four actual baseline/sshai client checks, including Bash stdin, stdout/stderr and exit 7.
Fixture integrity and exact census passed; guarded cleanup removed that preparation.
This single synthetic integration is **not** phase-wide readiness or live `codex exec`
parity. Windows has an administrator, FullLanguage PowerShell 7.6.3 session, but no
qualified restrictive execution profile. Its observed empty effective AppLocker policy
is not filesystem confinement. Neither platform has consumed a remote diagnostic or
assessor slot in this continuation.

The exact targets, paths and runtime observations remain private. Study consumption at
this preparation checkpoint is **40/120 diagnostic slots and 7/24 assessor contexts**.
There is no additional purchase, paid API fallback, model substitution or quota increase.

## Prospective Linux execution boundary

The selected preparation route uses existing namespace/chroot/privilege-drop tools,
not installation of an absent sandbox package. Its intended boundary is:

- A trusted controller fixes the actual host, SSH options and physical remote entry file.
  It invokes system OpenSSH; both arms investigate actual remote snapshots.
- The entry file handles untrusted command text and stdin only after confinement.
  A privileged setup script must never consume model command bodies as its own code.
- A per-case filesystem view exposes only the current read-only fixture, declared runtime
  dependencies and writable scratch. It uses mount, PID, network, IPC and UTS namespaces,
  an unprivileged numeric identity, cleared groups/capabilities and `no_new_privs`.
  Hostname/domain are synthetic within the verified distinct UTS namespace. Private proc
  requires `subset=pid`; live host-wide proc statistics are not exposed. No weaker fallback
  is permitted when these primitives are unavailable.
- The diagnostic model receives neither SSH keys nor an authentication-agent socket.
  Its existing local Codex profile keeps tool networking disabled and protects home,
  credentials, other cases, controller records and evaluator evidence.
- A client shim forwards bounded requests through two named FIFOs in authorized local
  scratch. The outside broker owns actual SSH credentials and fixes confinement regardless
  of whether the model modifies the shim or speaks the protocol directly. Filesystem
  communication is not permission to enable arbitrary network access.

The copied runtime inventory must include executable ELF `PT_INTERP` loaders and the
pinned sshai Bash epilogue dependencies (`env`, `base64`, `tr`), not merely primary tools.
Earlier no-model failures from a non-executable loader and a missing `base64` were
retained; corrected inventories used new preparations, not rewritten outcomes.

The pinned Darwin minimal-runtime policy permits reads under `/private/tmp` despite a
named deny. Actual negative probes detected this before any model launch. Controller
sockets therefore use short private roots under `/Users/Shared`, explicitly denied in
both-arm profiles except the exact workspace/executable exceptions. Known task-owned
SSH evidence was archived outside the readable tmp exception without rewriting original
result envelopes. Other minimal-runtime allowances remain a limitation, not a claim of
exclusive whole-OS access. No-model FIFO transactions are labeled separately from model
transactions in protected evidence.

No finite canary proves exhaustive confinement. Before launch, actual positive and
negative checks must cover both local-client access and the selected remote boundary,
including descendant access, traversal/symlinks, fixture modification, other cases and
synthetic protected-data categories. Real Bash, sshai probing/body execution and stdout,
stderr and exit behavior must work through the same selected path. Native `sandbox`
canaries alone do not establish live `codex exec` parity.

## Phase and evidence requirements

The original Linux allocation remains four excluded pilot slots (L01/L02, one pair each)
and 36 measured slots (L01–L06, three paired repetitions each). Seed 1010 fixes the
balanced pilot order and the original v3 Linux measurement projection. No replacement,
retry, outcome-based reordering or favorable-subset report is permitted.

The prospective phase must freeze exact source, executable, catalog, configuration,
fixture/prompt, runtime, shell, broker, access-profile, schedule, rubric and resource pins.
Preparation and no-model preflight are separate from launch. A successful fresh preflight,
manifest-bound record of actual task authorization and explicit launch flag are all
required; an approval boolean cannot substitute for access qualification.

Each slot is reserved once before its model attempt. Capture loss/overflow, unsafe access,
fixture mutation, observable model mismatch/rerouting and unsupported tool records stop
later reservations. Reserved-incomplete and failed outcomes remain retained. Finality,
semantic routing, usage, model grading and comparative eligibility are separate judgments.
Diagnostic and assessor usage remain separate; all model-visible guidance, errors,
recovery and artifact follow-ups count toward diagnostic usage.

Use the existing fixed model/reasoning and subscription-only limits: 600 seconds per
session and prospective observer capacity of 8 MiB per stream/native file and 4 MiB per
JSONL record. The common remote boundary caps combined diagnostic output at 1 MiB,
individual files at 16 MiB, address space at 512 MiB, UID-wide processes/threads at 128
and CPU at 60 seconds per process. These are not aggregate cgroup quotas. The controller
bounds diagnostic SSH calls at 29 seconds, broker callbacks at 30 seconds and requests
at 128 per session (including canary requests). Command and stdin bounds are 64 KiB and
1 MiB. These prospective controls do not alter historical defaults or earlier evidence.
No Linux result will be pooled with local or Windows results or interpreted as a causal
operating-system comparison.

## Windows prerequisite

The existing administrator SSH session is not the required read-restricted boundary.
At the pinned Codex source revision, the
[unelevated backend refuses restrictive reads](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/windows-sandbox-rs/src/lib.rs#L545-L553).
Its elevated setup changes identities, ACLs and firewall configuration; it must not be
invoked under the unchanged no-firewall-change restriction.

Windows preparation therefore needs an existing isolated SSH endpoint or a compatible,
qualified restrictive execution profile. A profileless AppContainer/LowBox mechanism
is an unqualified implementation possibility, not an available substitute. Microsoft
[documents LowBox token creation](https://learn.microsoft.com/en-us/windows/win32/secauthz/ntcreatelowboxtoken),
but this does not establish PowerShell startup, the required exclusive data view or
current endpoint qualification. Regular AppContainer compatibility grants also allow
some surrounding resources. [Profile registration](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createappcontainerprofile)
creates folders, ACLs and registry state; it is not a permissible silent fallback. Windows
command execution **and** sshai SCP/SFTP staging must be restricted to the same authorized
fixture/scratch boundary. Linux progress does not satisfy this prerequisite.
