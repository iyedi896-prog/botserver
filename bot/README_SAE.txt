Sae Uploader Telegram sign-in service  (SAE METHOD)

1. Extract the complete ZIP.
2. Double-click START_SAKO.bat.
3. Paste the token created for @sakoportal_bot from @BotFather when prompted.
4. Keep the console open while signing in.

The extension is LOCKED until you connect: tap any locked tool, press "Connect With Telegram" in the small window,
then press Start in the bot and approve with Yes (or reject with No). The extension polls the local pairing API
at http://127.0.0.1:8765. Never share bot/wexo_token.txt.

Bot commands (these three only - the command menu in Telegram shows exactly them):
  /start    welcome message (SAE METHOD)
  /me       your real account info: Name, Username, Telegram ID, Plan (Free / Pruimer / Legend)
  /pruimer  opens the Pruimer menu

There is no video download feature in the bot.

Activating Pruimer (only the Telegram account @Sakoaep):
1. Send the owner code to the bot (OWNER_CODE in wexo_telegram_bot.py).
2. The bot checks that the account really is @Sakoaep, then asks: "Write the ID of the user you want to give Pruimer to".
3. Write the user's Telegram ID (he must have pressed Connect in the extension once).
4. The bot answers "Choose" with two green buttons: 1month (30 days) / 3month (90 days).
5. Tap one: the user gets Pruimer for that time (a renewal is added after the time that is left) and receives a welcome message.
Any other account sending the code gets nothing. The extension shows the real remaining time as a countdown (Days / Hours / Minutes / Seconds)
and goes back to the price list by itself when it reaches zero.

Emoji: every emoji the bot writes is sent as a Telegram custom emoji from the packs in wexo_emoji_packs.txt when the bot can use them;
otherwise the normal emoji is shown automatically.
