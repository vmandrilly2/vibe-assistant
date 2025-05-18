# Vibe Assistant

They all talk about "vibe coding". I understood that was, you talk, and the ai writes the code. But I couldn't even find a suitable tool to do the STT part, so I created it myself.

What this really is: a Speech to text assistant (not just for coding)

## Features

- **App agnostic**: this tool can be used in any software or in the browser, it simulates typing.
- **STT**: press middle mouse button, talk, what you say is being written down right where your cursor clicked.
- **Realtime feedback**: during talking, little recognized bits of speech are shown in a tooltip
- **Instant translation**: hover the mic icon while pressing, select target language, now when you talk, what you say is in the tooltip, but what gets written down is the translation. Now I can talk in my native language (the only way to have good accuracy), but still get my stuff to the AI in English.

## Set up
You need to add a .env file at the roor and provide deepgramm API key. eg
DEEPGRAM_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Add also an Openai Api Key for the translation (it's currently set to use nano model for fast and cheap translation)
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

(Would be great to add more API supports in the future. It would be great to use local models although local models I tested are using way too much ressource for a worse accuracy and therefore impractical.)

## License

I didn't write the code, the AIs did, I just provided instructions, tested, and kept getting frustrated at the AI stupid mistakes next to the genius superhuman code writing abilities.

**Do whatever you want with the code**, just **don't be evil**. Remember to give a little bit to others some day, and remember to appreciate other beings have been giving us so much, which is the reason this app can work in the first place.

Hopefully some capable people will take in charge to make this project theirs and improve the tool, there is so much possibilities about what it could do! If you do, and if is open (like in Open Source, not like in OpenAI), then let me know, so I can benefit in my day to day use of the computer.
