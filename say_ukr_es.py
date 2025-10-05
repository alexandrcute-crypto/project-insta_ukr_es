import asyncio
import edge_tts

async def main():
    tts = edge_tts.Communicate("Привіт! Це тест озвучки українською мовою.", "uk-UA-PolinaNeural")
    await tts.save("test.mp3")

asyncio.run(main())
