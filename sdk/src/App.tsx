import "./App.css";
import { EmotionVoiceChat } from "./features/EmotionVoiceChat";

function App() {
  return (
    <>
      <EmotionVoiceChat
        apiKey={import.meta.env.VITE_EM_API_KEY ?? ""}
        companionId={import.meta.env.VITE_EM_COMPANION_ID ?? ""}
        baseUrl={import.meta.env.VITE_EM_BASE_URL}
      />
    </>
  );
}

export default App;
