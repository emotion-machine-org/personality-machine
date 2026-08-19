# Mobile Client (Expo)

Expo React Native app for chatting with companions: text chat, voice sessions (WebRTC/Pipecat), and a companion builder screen.

## Setup

```bash
npm install
npx expo start     # or: npm start
```

Then open the app in a development build, Android emulator, iOS simulator, or [Expo Go](https://expo.dev/go).

## Configuration

The app talks to the API server (default `http://localhost:8100`). Override with:

```bash
EXPO_PUBLIC_API_URL=http://your-host:8100 npx expo start
```

> On a physical device, `localhost` refers to the phone — set `EXPO_PUBLIC_API_URL` to your machine's LAN IP.

## Commands

```bash
npm start        # Expo dev server
npm run ios      # iOS simulator
npm run android  # Android emulator
npm run web      # Web target
npm run lint     # ESLint
```

## Structure

- `app/(tabs)/chat.tsx` — chat screen
- `app/(tabs)/builder.tsx` — companion builder
- `hooks/usePipecatSession.tsx` — voice session hook
- This project uses [Expo Router](https://docs.expo.dev/router/introduction) — files in `app/` are routes.
