import { afterEach, describe, expect, it, vi } from "vitest";
import { coreConversations, importConversations, sendCoreMessage } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("conversation core API", () => {
  it("searches the selected directory", async () => {
    const fetch = vi.fn().mockResolvedValue(
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
  });
});
