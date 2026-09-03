package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"
)

func followEvents(t *testing.T, b *bytes.Buffer) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(b.String()), "\n") {
		var e map[string]any
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			t.Fatal(err)
		}
		out = append(out, e)
	}
	return out
}
func TestFollowEmitterSplitUTF8AndPreMarker(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "MARK", "SENT")
	f.output([]byte("banner\x00 without newline")) // ignored, including binary
	f.output([]byte("\nMARK\n\xe2"))
	f.output([]byte("\x82\xac\n\nS"))
	f.output([]byte("ENT\nstate\n"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	e := followEvents(t, &b)
	if len(e) != 3 || e[0]["type"] != "started" || e[1]["data"] != "€\n" || e[2]["type"] != "completed" {
		t.Fatalf("events=%v", e)
	}
}

type slowFollowWriter struct {
	delay time.Duration
	bytes.Buffer
}

func (w *slowFollowWriter) Write(p []byte) (int, error) {
	time.Sleep(w.delay)
	return w.Buffer.Write(p)
}
func TestFollowEmitterNeverBlocksOnWriter(t *testing.T) {
	w := &slowFollowWriter{delay: 100 * time.Millisecond}
	f := newFollowEmitter(w, "h", "M", "S")
	start := time.Now()
	f.output([]byte("M\ntext\n"))
	if time.Since(start) > 20*time.Millisecond {
		t.Fatal("callback blocked on stderr")
	}
	f.output([]byte("\nS\n"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	if !strings.Contains(w.String(), `"type":"completed"`) {
		t.Fatal("completed missing")
	}
}

func TestFollowEmitterInvalidUnterminatedByteSuppressesOnce(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.output([]byte("M\n\xff"))
	f.output(bytes.Repeat([]byte("x"), 9000))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	e := followEvents(t, &b)
	var n int
	for _, x := range e {
		if x["type"] == "output_suppressed" {
			n++
			if x["reason"] != "invalid_utf8" {
				t.Fatalf("reason=%v", x["reason"])
			}
		}
	}
	if n != 1 {
		t.Fatalf("suppression events=%v", e)
	}
}

func TestFollowEmitterSuppressionFollowsAcceptedOutput(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.output([]byte("M\nprior\n\x00\n"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	e := followEvents(t, &b)
	if got, want := []string{e[0]["type"].(string), e[1]["type"].(string), e[2]["type"].(string), e[3]["type"].(string)}, []string{"started", "output", "output_suppressed", "completed"}; strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("event order=%v", got)
	}
	for i, event := range e {
		if int(event["seq"].(float64)) != i+1 {
			t.Fatalf("non-contiguous sequence: %v", e)
		}
	}
}

func TestFollowEmitterFragmentedCRSentinelAndUTF8Diagnostics(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "SENT")
	f.output([]byte("M\none\n\nSENT\r"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, strings.Repeat("€", 2000))
	e := followEvents(t, &b)
	if got := e[1]["data"]; got != "one\n" {
		t.Fatalf("delimiter leaked or user output changed: %q", got)
	}
	diagnostics := e[len(e)-1]["diagnostics"].(string)
	if len(diagnostics) > 4096 || !strings.HasSuffix(diagnostics, "€") {
		t.Fatalf("diagnostics is not rune-safe bounded UTF-8: bytes=%d tail=%q", len(diagnostics), diagnostics[len(diagnostics)-3:])
	}
}

func TestFollowEmitterHeartbeatCoalescingKeepsSequencesContiguous(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.mu.Lock()
	f.started = true
	f.startedAt = time.Now()
	for range 100 {
		f.event("heartbeat", nil)
	}
	f.mu.Unlock()
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	for i, event := range followEvents(t, &b) {
		if got := int(event["seq"].(float64)); got != i+1 {
			t.Fatalf("sequence gap at %d: %d", i, got)
		}
	}
}

func TestFollowEmitterHeartbeatStartsAfterInterval(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.setHeartbeatInterval(20 * time.Millisecond)
	f.output([]byte("M\n"))
	f.heartbeat()
	time.Sleep(25 * time.Millisecond)
	f.heartbeat()
	f.heartbeat()
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	e := followEvents(t, &b)
	var beats int
	for _, event := range e {
		if event["type"] == "heartbeat" {
			beats++
			if event["elapsed_ms"].(float64) < 20 {
				t.Fatalf("early heartbeat: %v", event)
			}
		}
	}
	if beats != 1 {
		t.Fatalf("heartbeats=%d events=%v", beats, e)
	}
}

func TestFollowEmitterPayloadBoundaryAndSuppressionOrder(t *testing.T) {
	for _, size := range []int{followPayloadLimit, followPayloadLimit + 1} {
		t.Run("payload", func(t *testing.T) {
			var b bytes.Buffer
			f := newFollowEmitter(&b, "h", "M", "S")
			f.output([]byte("M\n" + strings.Repeat("x", size)))
			f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
			var total, suppressed int
			seenSuppression := false
			for _, event := range followEvents(t, &b) {
				switch event["type"] {
				case "output":
					if seenSuppression {
						t.Fatal("output followed suppression")
					}
					total += len([]byte(event["data"].(string)))
				case "output_suppressed":
					seenSuppression = true
					suppressed++
				}
			}
			wantSuppressed := 0
			if size > followPayloadLimit {
				wantSuppressed = 1
			}
			if total != min(size, followPayloadLimit) || suppressed != wantSuppressed {
				t.Fatalf("size=%d total=%d suppressed=%d", size, total, suppressed)
			}
		})
	}
}

func TestFollowEmitterEscapedJSONStillBoundsRawPayload(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.output([]byte("M\n" + strings.Repeat("\\\"\n", 1000) + "\nS\n"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	for _, event := range followEvents(t, &b) {
		if event["type"] != "output" {
			continue
		}
		if n := len([]byte(event["data"].(string))); n > followEventLimit {
			t.Fatalf("raw payload=%d exceeds %d", n, followEventLimit)
		}
	}
}

func TestFollowEmitterLineBoundaryAndTrailingBlanks(t *testing.T) {
	for _, lines := range []int{followLineLimit, followLineLimit + 1} {
		t.Run(fmt.Sprintf("lines_%d", lines), func(t *testing.T) {
			var b bytes.Buffer
			f := newFollowEmitter(&b, "h", "M", "S")
			f.output([]byte("M\n" + strings.Repeat("line\n", lines) + "\nS\n"))
			f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
			var data strings.Builder
			var suppressed int
			for _, event := range followEvents(t, &b) {
				switch event["type"] {
				case "output":
					data.WriteString(event["data"].(string))
				case "output_suppressed":
					suppressed++
				}
			}
			if got := strings.Count(data.String(), "line\n"); got != min(lines, followLineLimit) {
				t.Fatalf("emitted lines=%d", got)
			}
			if want := min(1, lines-followLineLimit); suppressed != want {
				t.Fatalf("suppressed=%d want=%d", suppressed, want)
			}
		})
	}

	var capped bytes.Buffer
	c := newFollowEmitter(&capped, "h", "M", "S")
	c.output([]byte("M\n" + strings.Repeat("line\n", followLineLimit) + "unterminated-tail-that-exceeds-control-prefix-buffer"))
	c.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	var cappedData strings.Builder
	var cappedSuppression int
	for _, event := range followEvents(t, &capped) {
		if event["type"] == "output" {
			cappedData.WriteString(event["data"].(string))
		}
		if event["type"] == "output_suppressed" {
			cappedSuppression++
		}
	}
	if strings.Contains(cappedData.String(), "unterminated") || cappedSuppression != 1 {
		t.Fatalf("unterminated line 257 escaped: suppression=%d tail=%q", cappedSuppression, cappedData.String()[max(0, cappedData.Len()-64):])
	}

	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.output([]byte("M\nbody\n\n\n\nS\n")) // two user blanks plus wrapper delimiter
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	var data strings.Builder
	for _, event := range followEvents(t, &b) {
		if event["type"] == "output" {
			data.WriteString(event["data"].(string))
		}
	}
	if got, want := data.String(), "body\n\n\n"; got != want {
		t.Fatalf("blank-line output=%q want=%q", got, want)
	}
}

func TestFollowEmitterBoundsPreMarkerAndSuppressesBinaryOnce(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "MARK", "S")
	f.output(bytes.Repeat([]byte("x"), 32<<10))
	f.mu.Lock()
	if len(f.raw) > 8192 {
		t.Fatalf("pre-marker buffer=%d", len(f.raw))
	}
	f.mu.Unlock()
	f.output([]byte("\nMARK\nprior\n\x00more\n\x00again\n"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	var suppressions int
	for _, event := range followEvents(t, &b) {
		if event["type"] == "output_suppressed" {
			suppressions++
			if event["reason"] != "binary" {
				t.Fatalf("reason=%v", event["reason"])
			}
		}
	}
	if suppressions != 1 {
		t.Fatalf("suppressions=%d", suppressions)
	}
}

func TestFollowEmitterChunkAndRate(t *testing.T) {
	var b bytes.Buffer
	f := newFollowEmitter(&b, "h", "M", "S")
	f.output([]byte("M\n"))
	start := time.Now()
	f.output(bytes.Repeat([]byte("x"), followEventLimit*2))
	// writer framing/enqueue must not wait for the 100ms pump interval.
	if time.Since(start) > 20*time.Millisecond {
		t.Fatal("transport callback was rate-blocked")
	}
	f.output([]byte("\nS\n"))
	f.completed("/tmp", newPolicyDeniedOutcome(), exitPolicy, "")
	var prev time.Time
	for _, e := range followEvents(t, &b) {
		if e["type"] != "output" {
			continue
		}
		data := e["data"].(string)
		if len([]byte(data)) > followEventLimit {
			t.Fatalf("chunk=%d", len(data))
		}
		ts, _ := time.Parse(time.RFC3339Nano, e["timestamp"].(string))
		if !prev.IsZero() && ts.Sub(prev) < 90*time.Millisecond {
			t.Fatalf("output rate %s", ts.Sub(prev))
		}
		prev = ts
	}
}
