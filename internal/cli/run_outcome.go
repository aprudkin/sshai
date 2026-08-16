package cli

import "github.com/aprudkin/sshai/internal/artifact"

type runOutcomeKind uint8

const (
	runOutcomeInvalid runOutcomeKind = iota
	runOutcomeSuccess
	runOutcomeRemoteNonZero
	runOutcomeTransportFailure
	runOutcomePolicyDenied
	runOutcomeInternalFailure
)

// RunOutcome is the result of running one host. Saved outcomes always carry
// artifact metadata; policy and internal failures never do. Callers classify
// the result by Kind instead of interpreting a nil Meta together with an exit
// code.
type RunOutcome struct {
	kind         runOutcomeKind
	meta         artifact.Meta
	hasMeta      bool
	internalExit int
}

func newSavedRunOutcome(meta artifact.Meta) RunOutcome {
	kind := runOutcomeSuccess
	switch {
	case meta.TransportErr != "":
		kind = runOutcomeTransportFailure
	case meta.Exit != 0:
		kind = runOutcomeRemoteNonZero
	}
	return RunOutcome{kind: kind, meta: meta, hasMeta: true}
}

func newPolicyDeniedOutcome() RunOutcome {
	return RunOutcome{kind: runOutcomePolicyDenied}
}

func newInternalFailureOutcome(exitCode int) RunOutcome {
	return RunOutcome{kind: runOutcomeInternalFailure, internalExit: exitCode}
}

func (o RunOutcome) Kind() runOutcomeKind { return o.kind }

func (o RunOutcome) Meta() (artifact.Meta, bool) { return o.meta, o.hasMeta }

func (o RunOutcome) ArtifactID() string {
	if !o.hasMeta {
		return ""
	}
	return o.meta.ID
}

func (o RunOutcome) ExitCode() int {
	switch o.kind {
	case runOutcomeSuccess:
		return 0
	case runOutcomeRemoteNonZero:
		return o.meta.Exit
	case runOutcomeTransportFailure:
		return exitTransport
	case runOutcomePolicyDenied:
		return exitPolicy
	case runOutcomeInternalFailure:
		return o.internalExit
	default:
		return exitUsage
	}
}
