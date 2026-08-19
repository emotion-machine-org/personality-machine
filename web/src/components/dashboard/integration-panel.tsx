'use client';

import { useState } from 'react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import { useApiKeys } from '@/hooks/useApiKeys';
import CodeBlock from '@/components/ui/code-block';
import Icon from '@/components/ui/icon';
import type { CompanionConfig } from '@/lib/types';

type Tab = 'quickstart' | 'python' | 'curl' | 'agent';

interface IntegrationPanelProps {
  companionId: string | null;
  companionConfig?: CompanionConfig | null;
  companionName?: string;
  variant?: 'overlay' | 'page';
  onClose?: () => void;
}

export function IntegrationPanelContent({
  companionId,
  companionConfig,
  companionName = 'Companion',
  variant = 'overlay',
  onClose,
}: IntegrationPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>('quickstart');
  const [copiedId, setCopiedId] = useState(false);
  const { data: apiKeys, isLoading: apiKeysLoading } = useApiKeys();

  const hasApiKey = apiKeys && apiKeys.length > 0;
  const memoryEnabled = companionConfig?.memory?.enabled ?? false;
  const voiceEnabled = companionConfig?.voice?.preset !== undefined;

  const copyCompanionId = async () => {
    if (companionId) {
      await navigator.clipboard.writeText(companionId);
      setCopiedId(true);
      setTimeout(() => setCopiedId(false), 2000);
    }
  };

  const tabs: { id: Tab; label: string }[] = [
    { id: 'quickstart', label: 'Quick Start' },
    { id: 'python', label: 'Python' },
    { id: 'curl', label: 'curl' },
    { id: 'agent', label: 'AI Agent' },
  ];

  if (!companionId) {
    return (
      <div className="p-6 text-center text-white/60">
        <p>Select a companion to view integration options.</p>
      </div>
    );
  }

  return (
    <div className={cn(
      'flex flex-col w-full',
      variant === 'page' && 'max-w-2xl mx-auto'
    )}>
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-white/10">
        <div>
          <p className="text-sm font-light text-white/40">Integrate</p>
          <h2 className="text-xl font-light text-white/90 mt-0.5">
            Add {companionName} to your app
          </h2>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1 text-white/40 hover:text-white transition-colors"
          >
            <Icon name="x" size={20} />
          </button>
        )}
      </div>

      {/* API Key Status */}
      <div className="px-4 pt-4">
        {apiKeysLoading ? (
          <div className="p-3 bg-black/40 text-sm text-white/60">
            Loading API keys...
          </div>
        ) : !hasApiKey ? (
          <div className="p-3 bg-amber-500/10 border border-amber-500/20">
            <p className="text-sm text-amber-200 font-medium">API Key Required</p>
            <p className="text-sm text-white/60 mt-1">
              Create an API key to use the API.
            </p>
            <Link
              href="/api-keys"
              className="inline-flex items-center gap-1.5 mt-2 text-sm text-amber-300 hover:text-amber-200 transition-colors"
            >
              Go to API Keys
              <Icon name="arrow-right" size={14} />
            </Link>
          </div>
        ) : (
          <div className="p-3 bg-black/40 flex items-center justify-between">
            <div className="text-sm">
              <span className="text-white/60">API Key: </span>
              <span className="text-white font-mono">
                {apiKeys[0].prefix}****...****
              </span>
            </div>
            <Link
              href="/api-keys"
              className="text-xs text-white/50 hover:text-white/80 transition-colors"
            >
              Manage Keys
            </Link>
          </div>
        )}
      </div>

      {/* Companion ID */}
      <div className="px-4 pt-3">
        <div className="flex items-center gap-2 p-3 bg-black/40">
          <span className="text-sm text-white/60">Companion ID:</span>
          <code className="text-sm font-mono text-white flex-1 truncate">
            {companionId}
          </code>
          <button
            onClick={copyCompanionId}
            className="p-1 text-white/50 hover:text-white transition-colors"
            title="Copy companion ID"
          >
            {copiedId ? (
              <Icon name="check" size={14} />
            ) : (
              <Icon name="copy" size={14} />
            )}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-4 pt-4 border-b border-white/10">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-3 py-2 text-sm font-medium transition-colors',
              activeTab === tab.id
                ? 'text-white bg-white/10'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="p-4 flex-1 overflow-y-auto">
        {activeTab === 'quickstart' && (
          <QuickStartTab companionId={companionId} />
        )}
        {activeTab === 'python' && (
          <PythonTab companionId={companionId} />
        )}
        {activeTab === 'curl' && (
          <CurlTab companionId={companionId} />
        )}
        {activeTab === 'agent' && (
          <AgentTab
            companionId={companionId}
            companionName={companionName}
            companionConfig={companionConfig}
            memoryEnabled={memoryEnabled}
            voiceEnabled={voiceEnabled}
          />
        )}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-white/10">
        <a
          href="/API_V2_REFERENCE.md"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-sm text-white/60 hover:text-white transition-colors"
        >
          <Icon name="external-link" size={14} />
          Full API Reference
        </a>
      </div>
    </div>
  );
}

function QuickStartTab({ companionId }: { companionId: string }) {
  const code = `import asyncio
from emotion_machine import EmotionMachine

async def main():
    async with EmotionMachine(api_key="YOUR_API_KEY") as em:
        rel = em.relationship("${companionId}", "user_123")
        response = await rel.send("Hello!")
        print(response["message"]["content"])

asyncio.run(main())`;

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium text-white mb-2">Install the SDK</h3>
        <CodeBlock code="pip install emotion-machine" language="bash" />
      </div>

      <div>
        <h3 className="text-sm font-medium text-white mb-2">Send your first message</h3>
        <CodeBlock code={code} language="python" />
      </div>

      <p className="text-xs text-white/50">
        Replace <code className="bg-white/10 px-1 rounded">YOUR_API_KEY</code> with your actual API key
        and <code className="bg-white/10 px-1 rounded">user_123</code> with your user&apos;s ID.
      </p>
    </div>
  );
}

function PythonTab({ companionId }: { companionId: string }) {
  const basicCode = `from emotion_machine import EmotionMachine
import asyncio

async def main():
    async with EmotionMachine(api_key="YOUR_API_KEY") as em:
        rel = em.relationship("${companionId}", "user_123")

        # Send a message
        response = await rel.send("Hello!")
        print(response["message"]["content"])

asyncio.run(main())`;

  const streamingCode = `async def streaming_example():
    async with EmotionMachine(api_key="YOUR_API_KEY") as em:
        rel = em.relationship("${companionId}", "user_123")

        # Stream the response token by token
        async for chunk in rel.stream("Tell me a story"):
            data = chunk.get("data", {})
            if isinstance(data, dict) and data.get("type") == "delta":
                print(data.get("data", {}).get("content", ""), end="", flush=True)`;

  const profileCode = `async def profile_example():
    async with EmotionMachine(api_key="YOUR_API_KEY") as em:
        rel = em.relationship("${companionId}", "user_123")

        # Store user data in their profile
        await rel.profile_patch({
            "user": {"name": "Alice", "mood": "curious"}
        })

        # The companion will now know the user's name`;

  const errorCode = `from emotion_machine import APIError

try:
    response = await rel.send("Hello!")
except APIError as e:
    if e.status_code == 429:
        print("Rate limited - implement backoff")
    elif e.status_code == 401:
        print("Invalid API key")
    else:
        print(f"Error {e.status_code}: {e.message}")`;

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium text-white mb-2">Basic Usage</h3>
        <CodeBlock code={basicCode} language="python" />
      </div>

      <div>
        <h3 className="text-sm font-medium text-white mb-2">Streaming Responses</h3>
        <p className="text-xs text-white/50 mb-2">
          Stream tokens as they&apos;re generated for a chat UI.
        </p>
        <CodeBlock code={streamingCode} language="python" />
      </div>

      <div>
        <h3 className="text-sm font-medium text-white mb-2">User Profiles</h3>
        <p className="text-xs text-white/50 mb-2">
          Store structured data about users that persists across conversations.
        </p>
        <CodeBlock code={profileCode} language="python" />
      </div>

      <div>
        <h3 className="text-sm font-medium text-white mb-2">Error Handling</h3>
        <CodeBlock code={errorCode} language="python" />
      </div>
    </div>
  );
}

function CurlTab({ companionId }: { companionId: string }) {
  const sendMessage = `# Set your API key
export EM_API_KEY="YOUR_API_KEY"

# Send a message (creates relationship automatically)
curl -X POST "https://api.emotionmachine.ai/v2/companions/${companionId}/relationships/user_123/messages" \\
  -H "Authorization: Bearer $EM_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"content": "Hello!"}'`;

  const streamMessage = `# Stream the response
curl -X POST "https://api.emotionmachine.ai/v2/companions/${companionId}/relationships/user_123/messages" \\
  -H "Authorization: Bearer $EM_API_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Accept: text/event-stream" \\
  -d '{"content": "Tell me a story"}'`;

  const getRelationship = `# Get relationship details
curl "https://api.emotionmachine.ai/v2/companions/${companionId}/relationships/user_123" \\
  -H "Authorization: Bearer $EM_API_KEY"`;

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-medium text-white mb-2">Send a Message</h3>
        <CodeBlock code={sendMessage} language="bash" />
      </div>

      <div>
        <h3 className="text-sm font-medium text-white mb-2">Stream Response</h3>
        <p className="text-xs text-white/50 mb-2">
          Use Server-Sent Events for streaming.
        </p>
        <CodeBlock code={streamMessage} language="bash" />
      </div>

      <div>
        <h3 className="text-sm font-medium text-white mb-2">Get Relationship</h3>
        <CodeBlock code={getRelationship} language="bash" />
      </div>
    </div>
  );
}

function AgentTab({
  companionId,
  companionName,
  companionConfig,
  memoryEnabled,
  voiceEnabled,
}: {
  companionId: string;
  companionName: string;
  companionConfig?: CompanionConfig | null;
  memoryEnabled: boolean;
  voiceEnabled: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const systemPromptPreview = companionConfig?.system_prompt?.full_system_prompt
    ? companionConfig.system_prompt.full_system_prompt.slice(0, 200) +
      (companionConfig.system_prompt.full_system_prompt.length > 200 ? '...' : '')
    : 'Not configured';

  const agentInstructions = `# Emotion Machine Integration Guide

## Overview
You are integrating with "${companionName}", an AI companion powered by Emotion Machine.

## Setup

### 1. Environment Configuration
Add your API key to your \`.env\` file:
\`\`\`
EM_API_KEY=your_api_key_here
\`\`\`

**IMPORTANT**: Never commit API keys to version control. Ensure \`.env\` is in your \`.gitignore\`.

### 2. Install SDK
\`\`\`bash
pip install emotion-machine
\`\`\`

## Companion Details

- **Companion ID**: \`${companionId}\`
- **Name**: ${companionName}
- **Memory**: ${memoryEnabled ? 'Enabled (remembers conversations)' : 'Disabled'}
- **Voice**: ${voiceEnabled ? 'Enabled' : 'Text only'}

## Quick Integration

\`\`\`python
import os
from emotion_machine import EmotionMachine

async def chat_with_companion(user_id: str, message: str) -> str:
    """Send a message to the companion and get a response."""
    async with EmotionMachine(api_key=os.getenv("EM_API_KEY")) as em:
        rel = em.relationship("${companionId}", user_id)
        response = await rel.send(message)
        return response["message"]["content"]
\`\`\`

## Key Concepts

1. **Relationships**: Each user has a persistent relationship with the companion. Use a unique \`user_id\` per user.

2. **Memory**: The companion ${memoryEnabled ? 'remembers previous conversations and important facts about each user' : 'does not persist memory between sessions'}.

3. **Profile**: Store structured user data (preferences, settings) via \`rel.profile_patch()\`.

## Common Patterns

### User Authentication Flow
\`\`\`python
# Use your app's user ID as the relationship identifier
user_id = f"user_{your_auth_system_user_id}"
rel = em.relationship("${companionId}", user_id)
\`\`\`

### Streaming Responses (for chat UI)
\`\`\`python
async for chunk in rel.stream("Tell me about yourself"):
    data = chunk.get("data", {})
    if isinstance(data, dict) and data.get("type") == "delta":
        yield data.get("data", {}).get("content", "")  # Stream to frontend
\`\`\`

### Error Handling
\`\`\`python
from emotion_machine import APIError

try:
    response = await rel.send("Hello")
except APIError as e:
    if e.status_code == 429:
        # Rate limited - implement backoff
        pass
    elif e.status_code == 401:
        # Invalid API key
        pass
\`\`\`

## API Reference
Full documentation: ${typeof window !== 'undefined' ? window.location.origin : ''}/API_V2_REFERENCE.md`;

  const handleCopy = async () => {
    await navigator.clipboard.writeText(agentInstructions);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-4">
      <div className="p-4 bg-[color:var(--color-green-bg)]">
        <p className="text-xs font-light text-[color:var(--color-green-solid)] uppercase tracking-wide">
          For Claude Code, Codex, or similar AI coding agents
        </p>
        <p className="text-sm text-[color:var(--color-green-solid-hover)] mt-1.5 leading-relaxed">
          Copy these instructions and paste them into your AI agent&apos;s context.
          The agent will be able to integrate your companion into your codebase.
        </p>
      </div>

      <div className="relative">
        <div className="absolute top-2 right-2 z-10">
          <button
            onClick={handleCopy}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-all',
              copied
                ? 'bg-green-500/20 text-green-300'
                : 'bg-white/10 text-white hover:bg-white/15'
            )}
          >
            {copied ? (
              <>
                <Icon name="check" size={14} />
                Copied!
              </>
            ) : (
              <>
                <Icon name="copy" size={14} />
                Copy Instructions
              </>
            )}
          </button>
        </div>
        <CodeBlock
          code={agentInstructions}
          language="markdown"
          className="max-h-[400px] overflow-y-auto"
        />
      </div>

      <div className="p-3 bg-black/40">
        <h4 className="text-sm font-medium text-white mb-1">System Prompt Preview</h4>
        <p className="text-xs text-white/50 font-mono whitespace-pre-wrap">
          {systemPromptPreview}
        </p>
      </div>
    </div>
  );
}

export default IntegrationPanelContent;
