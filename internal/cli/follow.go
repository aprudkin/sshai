package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/aprudkin/sshai/internal/artifact"
)

const (
	followPayloadLimit = 64 << 10
	followLineLimit    = 256
	followEventLimit   = 4 << 10
	followOutputRate   = 100 * time.Millisecond
)

// followEmitter owns the ephemeral stderr JSONL stream for one invocation.
// Transport callbacks only frame and enqueue data; a separate pump rate-limits
// JSON writes so an SSH pipe is never held up by the preview consumer.
type followEmitter struct {
	mu                                       sync.Mutex
	w                                        io.Writer
	host, marker, sentinel                   string
	seq                                      int
	startedAt                                time.Time
	started, stopped, announced, partialLine bool
	heldBlanks                               int
	payload, lines                           int
	raw, queued                              []byte
	lastOutput, lastHeartbeat                time.Time
	heartbeatInterval                        time.Duration
	wake                                     chan struct{}
	stop                                     chan struct{}
	done                                     chan struct{}
	eventQ                                   [][]byte
	writeWake                                chan struct{}
	writerStop                               chan struct{}
	eventDone                                chan struct{}
	startedSignal                            chan struct{}
	closing                                  bool
	delayedSuppression                       string
}

func newFollowEmitter(w io.Writer, host, marker, sentinel string) *followEmitter {
	f := &followEmitter{w: w, host: host, marker: marker, sentinel: sentinel, wake: make(chan struct{}, 1), stop: make(chan struct{}), done: make(chan struct{}), writeWake: make(chan struct{}, 1), writerStop: make(chan struct{}), eventDone: make(chan struct{}), startedSignal: make(chan struct{})}
	go f.writer()
	go f.pump()
	return f
}
func (f *followEmitter) elapsed() int64 {
	if f.startedAt.IsZero() {
		return 0
	}
	return time.Since(f.startedAt).Milliseconds()
}
func (f *followEmitter) event(typ string, fields map[string]any) {
	// Only heartbeats may be discarded. Do this before allocating a sequence
	// number so every emitted record is contiguous.
	if typ == "heartbeat" && len(f.eventQ) >= 64 {
		return
	}
	f.seq++
	v := map[string]any{"schema_version": "v1", "seq": f.seq, "type": typ, "host": f.host, "timestamp": time.Now().UTC().Format(time.RFC3339Nano), "elapsed_ms": f.elapsed()}
	for k, x := range fields {
		v[k] = x
	}
	b, _ := json.Marshal(v)
	// All lifecycle, preview, and suppression records stay queued in order;
	// preview is bounded and heartbeats are the sole coalescible class.
	f.eventQ = append(f.eventQ, b)
	select {
	case f.writeWake <- struct{}{}:
	default:
	}
}

// writer serializes external stderr I/O without holding the emitter lock.
func (f *followEmitter) writer() {
	defer close(f.eventDone)
	for {
		select {
		case <-f.writeWake:
		case <-f.writerStop:
			for {
				f.mu.Lock()
				if len(f.eventQ) == 0 {
					f.mu.Unlock()
					return
				}
				b := f.eventQ[0]
				f.eventQ = f.eventQ[1:]
				f.mu.Unlock()
				_, _ = f.w.Write(append(b, '\n'))
			}
		}
		for {
			f.mu.Lock()
			if len(f.eventQ) == 0 {
				f.mu.Unlock()
				break
			}
			b := f.eventQ[0]
			f.eventQ = f.eventQ[1:]
			f.mu.Unlock()
			_, _ = f.w.Write(append(b, '\n'))
		}
	}
}

func (f *followEmitter) signal() {
	select {
	case f.wake <- struct{}{}:
	default:
	}
}
func (f *followEmitter) suppress(reason string) {
	if !f.announced {
		f.announced = true
		f.event("output_suppressed", map[string]any{"reason": reason})
	}
}

// delaySuppression preserves the contract that every accepted output event is
// emitted before its single suppression record. It is called with f.mu held.
func (f *followEmitter) delaySuppression(reason string) {
	if !f.announced && f.delayedSuppression == "" {
		f.delayedSuppression = reason
	}
	f.signal()
}

// output accepts arbitrary transport write boundaries. Before the exact remote
// marker it intentionally does nothing: SSH banners and wrapper noise are not
// live output and must not cause suppression.
func (f *followEmitter) output(p []byte) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.closing || f.stopped {
		return
	}
	f.raw = append(f.raw, p...)
	for {
		i := bytes.IndexByte(f.raw, '\n')
		if i < 0 {
			break
		}
		line := bytes.TrimSuffix(f.raw[:i], []byte{'\r'})
		f.raw = f.raw[i+1:]
		if !f.started {
			if bytes.Equal(line, []byte(f.marker)) {
				f.started = true
				f.startedAt = time.Now()
				close(f.startedSignal)
				f.event("started", nil)
			}
			continue
		}
		if bytes.Equal(line, []byte(f.sentinel)) {
			// The wrapper contributes precisely one delimiter blank; all
			// earlier consecutive blanks are user output.
			for i := 0; i < f.heldBlanks-1; i++ {
				f.accept([]byte("\n"), false)
			}
			f.stopped = true
			f.raw = nil
			f.heldBlanks = 0
			return
		}
		if len(line) == 0 {
			f.heldBlanks++
			continue
		}
		for i := 0; i < f.heldBlanks; i++ {
			f.accept([]byte("\n"), false)
		}
		f.heldBlanks = 0
		f.accept(append(append([]byte(nil), line...), '\n'), false)
	}
	// A peer may emit an unterminated banner forever. Retain only a bounded
	// suffix capable of containing the control line; no event is emitted yet.
	if !f.started && len(f.raw) > 8192 {
		f.raw = append([]byte(nil), f.raw[len(f.raw)-8192:]...)
		return
	}
	// Keep enough bytes to recognize a fragmented sentinel. This also frames
	// UTF-8 naturally: an incomplete rune is never handed to accept.
	if f.started && len(f.raw) > len(f.sentinel)+2 {
		n := len(f.raw) - (len(f.sentinel) + 2)
		n = validUTF8Prefix(f.raw, n)
		if n > 0 {
			f.accept(f.raw[:n], true)
			f.raw = append([]byte(nil), f.raw[n:]...)
		} else if len(f.raw) > 0 {
			// DecodeRune distinguishes an incomplete suffix (size 1 with a
			// valid leading byte) from an invalid byte, which is suppressed.
			_, size := utf8.DecodeRune(f.raw)
			if size == 1 && f.raw[0] >= utf8.RuneSelf {
				f.stopped = true
				f.delaySuppression("invalid_utf8")
			}
		}
	}
	if f.started && len(f.raw) > 8192 {
		f.stopped = true
		f.delaySuppression("invalid_utf8")
	}
}

func validUTF8Prefix(p []byte, n int) int {
	if n > len(p) {
		n = len(p)
	}
	for n > 0 && !utf8.Valid(p[:n]) {
		n--
	}
	return n
}
func (f *followEmitter) accept(p []byte, partial bool) {
	if f.stopped || len(p) == 0 {
		return
	}
	if bytes.IndexByte(p, 0) >= 0 {
		f.stopped = true
		f.delaySuppression("binary")
		return
	}
	if !utf8.Valid(p) {
		f.stopped = true
		f.delaySuppression("invalid_utf8")
		return
	}
	// A partial final line still consumes one of the 256 allowed lines. If
	// this chunk first terminates a previously admitted partial line, that
	// newline does not consume another line allowance.
	addLines := bytes.Count(p, []byte{'\n'})
	if f.partialLine && addLines > 0 {
		addLines--
		f.partialLine = false
	}
	if partial && p[len(p)-1] != '\n' && !f.partialLine {
		addLines++
		f.partialLine = true
	}
	room := followPayloadLimit - f.payload
	if room <= 0 || f.lines+addLines > followLineLimit {
		// A line is admitted atomically: a prefix of line 257 would itself
		// be a 257th line. Payload overflow may retain its allowed prefix.
		if room > 0 && f.lines+addLines <= followLineLimit {
			n := validUTF8Prefix(p, room)
			if n > 0 {
				f.queued = append(f.queued, p[:n]...)
				f.payload += n
				f.lines += bytes.Count(p[:n], []byte{'\n'})
			}
		}
		f.stopped = true
		f.delayedSuppression = "preview_limit"
		f.signal()
		return
	}
	if len(p) > room {
		n := validUTF8Prefix(p, room)
		if n > 0 {
			f.queued = append(f.queued, p[:n]...)
			f.payload += n
			f.lines += bytes.Count(p[:n], []byte{'\n'})
		}
		f.stopped = true
		f.delayedSuppression = "preview_limit"
		f.signal()
		return
	}
	f.payload += len(p)
	f.lines += addLines
	f.queued = append(f.queued, p...)
	f.signal()
}
func (f *followEmitter) nextChunkLocked() []byte {
	if len(f.queued) == 0 {
		return nil
	}
	n := len(f.queued)
	if n > followEventLimit {
		n = followEventLimit
	}
	n = validUTF8Prefix(f.queued, n)
	if n == 0 {
		return nil
	}
	q := append([]byte(nil), f.queued[:n]...)
	f.queued = f.queued[n:]
	return q
}
func (f *followEmitter) pump() {
	defer close(f.done)
	for {
		select {
		case <-f.wake:
		case <-f.stop:
			// Completion drains every accepted preview chunk in order. The rate
			// remains in force, but transport callbacks have already returned.
			for {
				f.mu.Lock()
				q := f.nextChunkLocked()
				if len(q) == 0 {
					f.emitDelayedSuppressionLocked()
					f.mu.Unlock()
					return
				}
				if d := followOutputRate - time.Since(f.lastOutput); d > 0 {
					f.mu.Unlock()
					time.Sleep(d)
					f.mu.Lock()
				}
				f.event("output", map[string]any{"stream": "combined", "data": string(q)})
				f.lastOutput = time.Now()
				f.mu.Unlock()
			}
		}
		f.mu.Lock()
		q := f.nextChunkLocked()
		if len(q) == 0 {
			f.emitDelayedSuppressionLocked()
			f.mu.Unlock()
			continue
		}
		if d := followOutputRate - time.Since(f.lastOutput); d > 0 {
			f.mu.Unlock()
			time.Sleep(d)
			f.mu.Lock()
		}
		f.event("output", map[string]any{"stream": "combined", "data": string(q)})
		f.lastOutput = time.Now()
		more := len(f.queued) > 0
		f.mu.Unlock()
		if more {
			time.AfterFunc(followOutputRate, f.signal)
		}
	}
}
func (f *followEmitter) emitDelayedSuppressionLocked() {
	if f.delayedSuppression == "" {
		return
	}
	f.suppress(f.delayedSuppression)
	f.delayedSuppression = ""
}

func (f *followEmitter) setHeartbeatInterval(interval time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.heartbeatInterval = interval
}

func (f *followEmitter) heartbeat() {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.started || f.closing {
		return
	}
	now := time.Now()
	if f.heartbeatInterval > 0 && (now.Sub(f.startedAt) < f.heartbeatInterval || (!f.lastHeartbeat.IsZero() && now.Sub(f.lastHeartbeat) < f.heartbeatInterval)) {
		return
	}
	f.event("heartbeat", nil)
	f.lastHeartbeat = now
}
func (f *followEmitter) finish() {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.started && !f.stopped {
		controlPrefix := isFollowControlPrefix(f.raw, f.sentinel)
		blanks := f.heldBlanks
		if controlPrefix && blanks > 0 {
			blanks--
		}
		for i := 0; i < blanks; i++ {
			f.accept([]byte("\n"), false)
		}
		f.heldBlanks = 0
		if len(f.raw) > 0 && !controlPrefix {
			f.accept(f.raw, true)
		}
		f.raw = nil
	}
}

type followOutcome struct {
	artifact.ResultEntry
	Kind string `json:"kind"`
}

func followOutcomeKindName(k runOutcomeKind) string {
	switch k {
	case runOutcomeSuccess:
		return "success"
	case runOutcomeRemoteNonZero:
		return "remote_nonzero"
	case runOutcomeLocalFailure:
		return "local_failure"
	case runOutcomeTransportFailure:
		return "transport_failure"
	case runOutcomeSetupFailure:
		return "setup_failure"
	case runOutcomePolicyDenied:
		return "policy_denied"
	case runOutcomeInternalFailure:
		return "internal_failure"
	default:
		return "invalid"
	}
}
func makeFollowOutcome(root, host string, o RunOutcome) followOutcome {
	r := followOutcome{ResultEntry: artifact.ResultEntry{Host: host, Exit: o.ExitCode()}, Kind: followOutcomeKindName(o.Kind())}
	if m, ok := o.Meta(); ok {
		r.ResultEntry = artifact.ResultEntryForMeta(root, m)
	}
	return r
}

// isFollowControlPrefix recognizes a delimiter line fragmented immediately
// before LF, including the CR in a CRLF line, without leaking it as output.
func isFollowControlPrefix(raw []byte, sentinel string) bool {
	return len(raw) > 0 && (bytes.HasPrefix([]byte(sentinel), raw) || bytes.Equal(raw, append([]byte(sentinel), '\r')))
}

func truncateUTF8(s string, max int) string {
	s = strings.ToValidUTF8(s, "\uFFFD")
	if len(s) <= max {
		return s
	}
	n := max
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}

func (f *followEmitter) completed(root string, outcome RunOutcome, processExit int, diagnostics string) {
	f.finish()
	f.mu.Lock()
	if !f.closing {
		f.closing = true
		close(f.stop)
	}
	f.mu.Unlock()
	<-f.done
	f.mu.Lock()
	defer f.mu.Unlock()
	f.emitDelayedSuppressionLocked()
	summary, _ := summarizeRunOutcomes([]RunOutcome{outcome})
	diagnostics = truncateUTF8(diagnostics, 4096)
	f.event("completed", map[string]any{"outcome": makeFollowOutcome(root, f.host, outcome), "summary": summary, "process_exit": processExit, "diagnostics": diagnostics})
	close(f.writerStop)
	f.mu.Unlock()
	<-f.eventDone
	f.mu.Lock()
}
func followUnavailable(stderr io.Writer) int {
	fmt.Fprintln(stderr, "run: --follow requires a streaming transport")
	return exitUsage
}
