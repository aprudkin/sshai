package cli

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"strconv"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
)

type resultModeOptions struct {
	format    string
	resultOut string
}

type hostRunResult struct {
	outcome RunOutcome
	stdout  bytes.Buffer
	stderr  bytes.Buffer
}

// writeRunResults is the invocation-wide output controller for one or many
// hosts. It owns human/JSON selection, deterministic buffer flushing, internal
// failure fallback, aggregation, envelope rendering, and side-file publication.
func writeRunResults(root string, runs []hostRunResult, mode resultModeOptions, stdout, stderr io.Writer) int {
	outcomes := make([]RunOutcome, len(runs))
	internalFailed := false
	for i := range runs {
		outcomes[i] = runs[i].outcome
		internalFailed = internalFailed || runs[i].outcome.Kind() == runOutcomeInternalFailure
	}

	if mode.format != "json" || internalFailed {
		exitCode := writeHumanRunResults(runs, stdout, stderr)
		if mode.format == "json" {
			// An unsaved internal failure breaks the v1 runs/hosts invariant, so
			// JSON mode preserves the established human fallback and exit 96.
			return exitUsage
		}
		return exitCode
	}

	for i := range runs {
		_, _ = stderr.Write(runs[i].stderr.Bytes())
	}
	return writeResultMode(root, outcomes, mode.resultOut, stdout, stderr)
}

func writeHumanRunResults(runs []hostRunResult, stdout, stderr io.Writer) int {
	var okCount, failedCount, transportErrCount, setupErrCount int
	var sawLocalError, sawSetupErr, sawTransportErr, sawPolicyDenied bool
	maxRemoteExit := 0

	for i := range runs {
		if i > 0 {
			fmt.Fprintln(stdout)
		}
		_, _ = stdout.Write(runs[i].stdout.Bytes())
		_, _ = stderr.Write(runs[i].stderr.Bytes())

		switch runs[i].outcome.Kind() {
		case runOutcomeSuccess:
			okCount++
		case runOutcomeLocalFailure:
			failedCount++
			sawLocalError = true
			maxRemoteExit = max(maxRemoteExit, runs[i].outcome.ExitCode())
		case runOutcomeTransportFailure:
			transportErrCount++
			sawTransportErr = true
		case runOutcomeSetupFailure:
			failedCount++
			setupErrCount++
			sawSetupErr = true
		case runOutcomePolicyDenied:
			failedCount++
			sawPolicyDenied = true
		case runOutcomeRemoteNonZero, runOutcomeInternalFailure, runOutcomeInvalid:
			failedCount++
			maxRemoteExit = max(maxRemoteExit, runs[i].outcome.ExitCode())
		}
	}

	if len(runs) > 1 {
		fmt.Fprintf(stdout, "hosts=%d ok=%d failed=%d transport-errors=%d", len(runs), okCount, failedCount, transportErrCount)
		if setupErrCount > 0 {
			fmt.Fprintf(stdout, " setup-errors=%d", setupErrCount)
		}
		fmt.Fprintln(stdout)
	}
	switch {
	case sawLocalError:
		return exitUsage
	case sawSetupErr:
		return exitSetup
	case sawTransportErr:
		return exitTransport
	case sawPolicyDenied:
		return exitPolicy
	default:
		return maxRemoteExit
	}
}

// summarizeRunOutcomes builds the unchanged v1 summary and runs slice from
// typed per-host outcomes. Policy-denied hosts have no artifact and stay out
// of runs. Internal failures are counted as failed, but callers must not render
// a JSON envelope containing them because no artifact exists for that host.
func summarizeRunOutcomes(outcomes []RunOutcome) (artifact.Summary, []artifact.Meta) {
	summary := artifact.Summary{Hosts: len(outcomes)}
	saved := make([]artifact.Meta, 0, len(outcomes))
	for _, outcome := range outcomes {
		meta, hasMeta := outcome.Meta()
		if hasMeta {
			saved = append(saved, meta)
		}
		switch outcome.Kind() {
		case runOutcomeSuccess:
			summary.OK++
		case runOutcomeRemoteNonZero:
			summary.Failed++
			summary.WorstExit = max(summary.WorstExit, meta.Exit)
		case runOutcomeLocalFailure:
			summary.Failed++
			summary.LocalErrors++
			summary.WorstExit = max(summary.WorstExit, exitUsage)
		case runOutcomeTransportFailure:
			summary.TransportErrors++
		case runOutcomeSetupFailure:
			summary.Failed++
			summary.SetupErrors++
		case runOutcomePolicyDenied:
			summary.PolicyDenied++
		case runOutcomeInternalFailure, runOutcomeInvalid:
			summary.Failed++
		}
	}
	return summary, saved
}

// renderResultEnvelope aggregates outcomes and renders one unchanged v1 JSON
// document. The caller supplies the batch id so rendering remains directly
// testable without weakening production batch-id generation.
func renderResultEnvelope(root string, outcomes []RunOutcome, batchID string) ([]byte, artifact.Summary) {
	summary, saved := summarizeRunOutcomes(outcomes)
	return artifact.RenderResult(root, saved, summary, batchID), summary
}

func resultModeExitCode(summary artifact.Summary) int {
	switch {
	case summary.LocalErrors > 0:
		return exitUsage
	case summary.SetupErrors > 0:
		return exitSetup
	case summary.TransportErrors > 0:
		return exitTransport
	case summary.PolicyDenied > 0:
		return exitPolicy
	default:
		return summary.WorstExit
	}
}

// writeResultMode is the shared single-host and fan-out JSON path. It writes
// stdout before attempting the side file, preserving the established failure
// ordering and leaving worker diagnostic flushing to the run controller.
func writeResultMode(root string, outcomes []RunOutcome, resultOut string, stdout, stderr io.Writer) int {
	document, summary := renderResultEnvelope(root, outcomes, newBatchID())
	_, _ = stdout.Write(document)
	_, _ = stdout.Write([]byte("\n"))
	if code := writeResultOut(resultOut, document, stderr); code != 0 {
		return code
	}
	return resultModeExitCode(summary)
}

// newBatchID returns a crypto-random "a"+32-hex-chars correlation id, the
// same shape as artifact ids so a consumer can treat batch_id uniformly.
func newBatchID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		// crypto/rand cannot fail on supported platforms; degenerate to a
		// time-based id rather than block.
		return "a" + strconv.FormatInt(time.Now().UnixNano(), 16)
	}
	return "a" + hex.EncodeToString(b)
}
