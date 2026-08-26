import { afterEach, describe, expect, it, vi } from "vitest";
import {
  completeMailboxMessage,
  coreConversations,
  importConversations,
  mailbox,
  refreshConversation,
  sendCoreMessage,
  sendProviderTurn,
  stopMailboxListener,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("conversation core API", () => {
  it("searches the selected directory", async () => {
    const fetch = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    await coreConversations("socket race");
    expect(fetch.mock.calls[0][0]).toContain("/conversations?q=socket+race");
  });

  it("selects candidates and sends conversation messages", async () => {
    const fetch = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ message_id: "message-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    await importConversations(["conversation-1"]);
    await sendCoreMessage({ body: "hello", target_conversation_id: "conversation-1" });
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      conversation_ids: ["conversation-1"],
    });
    expect(JSON.parse(fetch.mock.calls[1][1].body).body).toBe("hello");
    expect(JSON.parse(fetch.mock.calls[1][1].body)).not.toHaveProperty("delivery_strategy");
  });

  it("uses distinct mailbox and provider control endpoints", async () => {
    const fetch = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);

    await mailbox("chat/1", "pending");
    await stopMailboxListener("chat/1");
    await completeMailboxMessage("mail/1", { outcome: "blocked", detail: "Need input" });
    await refreshConversation("chat/1", 12);
    await sendProviderTurn("chat/1", { prompt: "Run checks", effort: "high" });

    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/mailbox/chat%2F1?state=pending",
      "/api/v1/mailbox/chat%2F1/stop-listener",
      "/api/v1/messages/mail%2F1/complete",
      "/api/v1/conversations/chat%2F1/refresh",
      "/api/v1/conversations/chat%2F1/turns",
    ]);
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
      outcome: "blocked",
      detail: "Need input",
    });
    expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({ wait_seconds: 12 });
  });
});
