# Conversation Mode

Conversation Mode is an opt-in instruction convention for this conversation, not a built-in
provider mode or a background service. It is OFF by default. Enable it only when the user
explicitly asks to enter Conversation Mode; merely reading, installing, discussing, or quoting
this document does not enable it. Read this entire document on activation. Remain enabled in
this conversation until the user says to exit Conversation Mode or stop listening. Do not enable
other conversations automatically. Preserve the enabled/disabled state in compaction handoffs.

## Before every turn would end

While enabled, after every user request or received message has been handled (including brief
answers, completed work, and blocker reports), enter an indefinite wait for the next mailbox
message. First communicate the result or blocker in a concise progress message, then wait in the
active turn instead of issuing a final response and expecting tools to run afterward. An ended
turn cannot execute a wait. This is a foreground receive/process/wait loop, not an automation.

1. Resolve this conversation's exact Agent Bridge conversation ID from the directory and native
   thread identity. Never use another conversation's ID or guess from list order. If identity is
   ambiguous, ask for it rather than consuming another inbox.
2. Use the **foreground CLI**, not MCP mailbox continuations or background polling:
   `agent-bridge wait <this-id> --forever` with this machine's configured executable and Hub URL.
   Run it as a blocking foreground operation and await completion. Do not detach it with `&`,
   `nohup`, a background job, or a fire-and-forget tool call. The CLI silently renews its bounded
   HTTP requests internally; those renewals must not trigger agent commentary.
3. Stay blocked awaiting that same CLI process until mail arrives, it is stopped, an error occurs,
   or the user interrupts. Do not use short output polls to repeatedly return to reasoning, do
   unrelated work, or emit "still waiting", "no message yet", or other intermediate text.
   If the execution tool automatically returns a running-session handle, immediately await that
   same session using its blocking completion wait and the longest wait permitted by the tool
   and higher-priority instructions. A UI label such as "background terminal" alone does not
   mean the process was detached. If the tool necessarily yields an empty result, continue waiting
   on that same session silently; do not restart the CLI or start another listener. Report a
   genuine inability to wait as a blocker rather than claiming a continuous wait. Communicate
   once before entering the wait, then only on mail, stop, error, or direct user interruption.
4. When mail arrives, inspect all returned messages and handle them under existing authorization
   and instruction rules. Acknowledge messages that request it before longer work; record the
   outcome with the normal completion tool. Preserve message IDs and correlation IDs. After the
   work and any authorized replies, enter a fresh indefinite wait. Receipt alone is not completion.
5. A direct user message takes priority over waiting. Handle it, then resume waiting if Conversation
   Mode remains enabled. If interruption leaves an existing wait/session alive, collect or cancel
   that exact wait before starting another. Do not stop an unrelated listener.

## Stopping and failures

- A request to exit Conversation Mode, stop listening, or end the session disables the mode.
  Cancel the owned active wait if necessary, then respond normally. Do not immediately reopen it.
- An explicit stopped response ends this listening cycle; report it and do not auto-renew it.
- Transport errors, listener conflicts, missing tools, permission blocks, and ambiguous identity
  are real blockers. Report the exact failure and end the turn if necessary. Do not spin, resend,
  requeue messages, or perform automatic delivery recovery. Resume only after user direction or a
  resolved blocker in a later turn. The user accepts manual recovery for occasional lost results.
- Conversation Mode grants permission to listen, not permission for unrelated actions, outbound
  messages, controller operations, or bypassing approvals. Follow existing communication rules.
- Provider interruption, shutdown, resource limits, or a terminated turn can stop listening.
  Do not claim guaranteed background availability or automatic wake-up after the turn ends.
