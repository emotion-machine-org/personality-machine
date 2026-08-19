'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import Icon from '@/components/ui/icon';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface MiniChatProps {
  isOpen: boolean;
  onToggle: () => void;
  onSend: (message: string, history: ChatMessage[]) => Promise<string>;
  isLoading?: boolean;
}

export function MiniChat({ isOpen, onToggle, onSend, isLoading = false }: MiniChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input when chat opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  const handleSend = useCallback(async () => {
    const message = inputValue.trim();
    if (!message || isSending) return;

    const userMessage: ChatMessage = { role: 'user', content: message };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsSending(true);

    try {
      const response = await onSend(message, messages);
      const assistantMessage: ChatMessage = { role: 'assistant', content: response };
      setMessages((prev) => [...prev, assistantMessage]);
    } catch (error) {
      console.error('Failed to send message:', error);
      const errorMessage: ChatMessage = {
        role: 'assistant',
        content: 'Sorry, something went wrong. Please try again.',
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setIsSending(false);
    }
  }, [inputValue, isSending, messages, onSend]);

  // Large black circular FAB button when closed (176px)
  if (!isOpen) {
    return (
      <button
        onClick={onToggle}
        disabled={isLoading}
        className="fixed bottom-[32px] right-[32px] w-[176px] h-[176px] rounded-full bg-black flex items-center justify-center z-50 shadow-2xl hover:scale-105 transition-transform disabled:opacity-50"
        title="Test memory with chat"
      >
        <Icon name="message-text" size={48} className="text-white/60" />
      </button>
    );
  }

  // Chat panel when open
  return (
    <div className="fixed bottom-[32px] right-[32px] w-[400px] h-[500px] bg-black flex flex-col z-50 shadow-2xl">
      {/* Header */}
      <div className="flex items-center justify-between px-[20px] py-[16px] shrink-0">
        <div className="flex items-center gap-[10px]">
          <Icon name="message-text" size={20} className="text-white/60" />
          <span className="text-[16px] font-light text-white">Test Memory</span>
        </div>
        <button
          onClick={onToggle}
          className="p-[4px] text-white/40 hover:text-white transition-colors"
        >
          <Icon name="x" size={20} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-[20px] py-[16px]">
        {messages.length === 0 && (
          <div className="text-center text-white/30 text-[14px] py-[20px]">
            Send a message to test memory consolidation.
            <br />
            New memories will be highlighted in the list.
          </div>
        )}
        {messages.map((msg, i) =>
          msg.role === 'user' ? (
            <div key={i} className="flex flex-col items-end mb-[16px]">
              <p className="text-[18px] font-book leading-relaxed text-white text-right max-w-[85%]">
                {msg.content}
              </p>
            </div>
          ) : (
            <div key={i} className="flex flex-col items-start mb-[16px]">
              <div className="max-w-[85%] bg-[#161616] rounded-lg px-3 py-2">
                <p className="text-[18px] leading-relaxed text-white">
                  {msg.content}
                </p>
              </div>
            </div>
          )
        )}
        {isSending && (
          <div className="flex flex-col items-start mb-[16px]">
            <div className="max-w-[85%] bg-[#161616] rounded-lg px-3 py-2">
              <p className="text-[18px] leading-relaxed text-white/50">
                Thinking...
              </p>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input - matches ChatInput styling */}
      <div className="shrink-0 p-[16px]">
        <div className="flex items-center gap-3">
          <div className="flex-1 rounded-full bg-[#3C3C3C] px-4 py-3">
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder="Type a message..."
              disabled={isSending}
              className="w-full bg-transparent text-white placeholder:text-white/40 focus:outline-none disabled:opacity-50"
            />
          </div>
          <button
            onClick={handleSend}
            disabled={!inputValue.trim() || isSending}
            className={`flex h-12 w-12 items-center justify-center rounded-full transition ${
              !inputValue.trim() || isSending
                ? 'bg-[#3C3C3C] text-white/30 cursor-not-allowed'
                : 'bg-white text-black hover:bg-white/90'
            }`}
          >
            <Icon name="arrow-up" size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
