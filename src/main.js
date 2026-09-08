export default async ({ req, res, log, error }) => {
  const VERIFY_TOKEN = process.env.VERIFY_TOKEN;
  const ACCESS_TOKEN = process.env.WHATSAPP_ACCESS_TOKEN;
  const PHONE_NUMBER_ID = process.env.WHATSAPP_PHONE_NUMBER_ID;

  // WhatsApp webhook verification
  if (req.method === "GET") {
    const mode = req.query?.["hub.mode"];
    const token = req.query?.["hub.verify_token"];
    const challenge = req.query?.["hub.challenge"];

    if (mode === "subscribe" && token === VERIFY_TOKEN) {
      log("Webhook verification successful");
      return res.text(challenge ?? "");
    }

    return res.text("Forbidden", 403);
  }

  // Only POST is allowed for incoming messages
  if (req.method !== "POST") {
    return res.text("Method Not Allowed", 405);
  }

  try {
    const body = req.bodyJson ?? {};

    const message =
      body?.entry?.[0]?.changes?.[0]?.value?.messages?.[0];

    // Ignore events that aren't messages
    if (!message) {
      return res.text("EVENT_RECEIVED", 200);
    }

    const from = message.from;

    // For now, only handle text messages
    if (message.type !== "text") {
      return res.text("EVENT_RECEIVED", 200);
    }

    const incomingText = message.text?.body?.trim() ?? "";

    log(`Incoming message from ${from}: ${incomingText}`);

    const reply =
      `Hello 👋\n\n` +
      `Main Yash ka WhatsApp bot hoon 🤖\n` +
      `Aapne kaha: "${incomingText}"`;

    const response = await fetch(
      `https://graph.facebook.com/v23.0/${PHONE_NUMBER_ID}/messages`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${ACCESS_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          messaging_product: "whatsapp",
          recipient_type: "individual",
          to: from,
          type: "text",
          text: {
            preview_url: false,
            body: reply
          }
        })
      }
    );

    const result = await response.json();

    if (!response.ok) {
      error(`WhatsApp API error: ${JSON.stringify(result)}`);
      return res.json(
        { ok: false, error: result },
        500
      );
    }

    log(`Reply sent to ${from}`);

    return res.json({ ok: true });

  } catch (err) {
    error(`Function error: ${err?.stack || err}`);

    return res.json(
      {
        ok: false,
        error: "Internal server error"
      },
      500
    );
  }
};
