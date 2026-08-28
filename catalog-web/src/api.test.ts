import { afterEach, describe, expect, it, vi } from "vitest";
import {
  acknowledgeMailboxMessage,
  completeMailboxMessage,
  coreMessage,
  coreConversations,
  importConversations,
  mailbox,
  refreshConversation,
  sendCoreMessage,
  sendProviderTurn,
  stopMailboxListener,
  waitForMessageReceipt,
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
    await sendCoreMessage({
      body: "hello",
      source_conversation_id: "conversation-0",
      target_conversation_id: "conversation-1",
      acknowledgement_requested: true,
    });
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      conversation_ids: ["conversation-1"],
    });
    expect(JSON.parse(fetch.mock.calls[1][1].body).body).toBe("hello");
    expect(JSON.parse(fetch.mock.calls[1][1].body).acknowledgement_requested).toBe(true);
    expect(JSON.parse(fetch.mock.calls[1][1].body)).not.toHaveProperty("delivery_strategy");
  });

  it("acknowledges, inspects, and waits for requested receipts", async () => {
    const fetch = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify({ message_id: "mail/1", status: "reached" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);

    await acknowledgeMailboxMessage("chat/2", "mail/1", "Starting work");
    await coreMessage("mail/1");
    await waitForMessageReceipt("chat/1", "mail/1", {
      until: "acknowledged",
      timeout_seconds: 3600,
      after_revision: 2,
    });

    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/messages/mail%2F1/acknowledge",
      "/api/v1/messages/mail%2F1",
      "/api/v1/messages/mail%2F1/wait-receipt",
    ]);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      conversation_id: "chat/2",
      detail: "Starting work",
    });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
      source_conversation_id: "chat/1",
      until: "acknowledged",
      timeout_seconds: 3600,
      after_revision: 2,
    });
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
    await completeMailboxMessage("chat/1", "mail/1", { outcome: "blocked", detail: "Need input" });
    await refreshConversation("chat/1", 12);
    await sendProviderTurn("chat/1", { prompt: "Run checks", effort: "high" });

    expect(fetch.mock.calls.map((call) => call[0])).toEqual([
      "/api/v1/mailbox/chat%2F1?state=pending",
      "/api/v1/mailbox/chat%2F1/stop-listener",
      "/api/v1/messages/mail%2F1/complete",
      "/api/v1/conversations/chat%2F1/refresh?wait_seconds=12",
      "/api/v1/conversations/chat%2F1/turns",
    ]);
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({
      conversation_id: "chat/1",
      outcome: "blocked",
      detail: "Need input",
    });
    expect(fetch.mock.calls[3][1].body).toBeUndefined();
  });
});
