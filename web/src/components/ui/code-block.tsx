'use client';

import { useState } from 'react';
import { cn } from '@/lib/utils';
import Icon from './icon';

interface CodeBlockProps {
  code: string;
  language?: 'python' | 'bash' | 'typescript' | 'json' | 'markdown';
  showLineNumbers?: boolean;
  className?: string;
  title?: string;
}

export default function CodeBlock({
  code,
  language = 'python',
  showLineNumbers = false,
  className,
  title,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = code.split('\n');

  return (
    <div className={cn('relative group', className)}>
      {title && (
        <div className="flex items-center justify-between px-3 py-2 bg-white/5 border-b border-white/10 text-xs text-white/60">
          <span>{title}</span>
          <span className="uppercase tracking-wider">{language}</span>
        </div>
      )}
      <div className="relative">
        <pre className="overflow-x-auto p-4 text-sm font-mono bg-black/40 text-white/90">
          <code>
            {showLineNumbers ? (
              lines.map((line, i) => (
                <div key={i} className="flex">
                  <span className="select-none text-white/30 w-8 mr-4 text-right">
                    {i + 1}
                  </span>
                  <span className="flex-1">
                    <SyntaxHighlight code={line} language={language} />
                  </span>
                </div>
              ))
            ) : (
              <SyntaxHighlight code={code} language={language} />
            )}
          </code>
        </pre>
        <button
          onClick={handleCopy}
          className={cn(
            'absolute top-2 right-2 p-2 transition-all',
            'bg-white/5 hover:bg-white/10 text-white/60 hover:text-white',
            'opacity-0 group-hover:opacity-100'
          )}
          title="Copy to clipboard"
        >
          {copied ? (
            <Icon name="check" size={14} />
          ) : (
            <Icon name="copy" size={14} />
          )}
        </button>
      </div>
    </div>
  );
}

// Simple syntax highlighting without external dependencies
function SyntaxHighlight({ code, language }: { code: string; language: string }) {
  if (language === 'markdown') {
    return <span className="whitespace-pre-wrap">{code}</span>;
  }

  // Token patterns for different languages
  const patterns: Record<string, Array<{ pattern: RegExp; className: string }>> = {
    python: [
      { pattern: /(#.*)$/gm, className: 'text-white/40' }, // Comments
      { pattern: /\b(from|import|async|await|with|as|def|class|return|if|else|elif|for|in|try|except|raise|True|False|None)\b/g, className: 'text-purple-400' }, // Keywords
      { pattern: /\b(print|str|int|float|list|dict|len|range|type|isinstance)\b/g, className: 'text-blue-400' }, // Built-ins
      { pattern: /(["'])(?:(?=(\\?))\2.)*?\1/g, className: 'text-green-400' }, // Strings
      { pattern: /\b(\d+\.?\d*)\b/g, className: 'text-orange-400' }, // Numbers
      { pattern: /\b(EmotionMachine|APIError|WebSocketError)\b/g, className: 'text-yellow-400' }, // Custom classes
    ],
    bash: [
      { pattern: /(#.*)$/gm, className: 'text-white/40' }, // Comments
      { pattern: /\b(export|curl|pip|npm|cd|echo|cat|mkdir|uv|run|install)\b/g, className: 'text-purple-400' }, // Commands
      { pattern: /(?:^|\s)(-[A-Za-z]|--[A-Za-z][A-Za-z-]*)(?=\s|$)/g, className: 'text-blue-400' }, // Flags (must follow whitespace)
      { pattern: /(["'])(?:(?=(\\?))\2.)*?\1/g, className: 'text-green-400' }, // Strings
      { pattern: /(\$[A-Z_]+)/g, className: 'text-yellow-400' }, // Environment variables
      { pattern: /(https?:\/\/[^\s"']+)/g, className: 'text-cyan-400' }, // URLs
    ],
    typescript: [
      { pattern: /(\/\/.*)$/gm, className: 'text-white/40' }, // Comments
      { pattern: /\b(import|export|from|const|let|var|function|async|await|return|if|else|for|of|in|try|catch|throw|new|class|interface|type)\b/g, className: 'text-purple-400' }, // Keywords
      { pattern: /(["'`])(?:(?=(\\?))\2.)*?\1/g, className: 'text-green-400' }, // Strings
      { pattern: /\b(\d+\.?\d*)\b/g, className: 'text-orange-400' }, // Numbers
      { pattern: /\b(string|number|boolean|void|any|null|undefined)\b/g, className: 'text-blue-400' }, // Types
    ],
    json: [
      { pattern: /(["'])(?:(?=(\\?))\2.)*?\1(?=\s*:)/g, className: 'text-cyan-400' }, // Keys
      { pattern: /:\s*(["'])(?:(?=(\\?))\2.)*?\1/g, className: 'text-green-400' }, // String values
      { pattern: /:\s*(\d+\.?\d*)/g, className: 'text-orange-400' }, // Number values
      { pattern: /:\s*(true|false|null)/g, className: 'text-purple-400' }, // Boolean/null
    ],
  };

  const langPatterns = patterns[language] || [];

  if (langPatterns.length === 0) {
    return <span className="whitespace-pre-wrap">{code}</span>;
  }

  // Simple approach: apply patterns sequentially
  const replacements: Array<{ start: number; end: number; html: string }> = [];

  langPatterns.forEach(({ pattern, className }) => {
    const regex = new RegExp(pattern.source, pattern.flags);
    let match;
    while ((match = regex.exec(code)) !== null) {
      replacements.push({
        start: match.index,
        end: match.index + match[0].length,
        html: `<span class="${className}">${escapeHtml(match[0])}</span>`,
      });
    }
  });

  // Sort by start position and filter overlapping
  replacements.sort((a, b) => a.start - b.start);
  const filtered: typeof replacements = [];
  let lastEnd = 0;
  for (const r of replacements) {
    if (r.start >= lastEnd) {
      filtered.push(r);
      lastEnd = r.end;
    }
  }

  // Build result
  let html = '';
  let pos = 0;
  for (const r of filtered) {
    if (r.start > pos) {
      html += escapeHtml(code.slice(pos, r.start));
    }
    html += r.html;
    pos = r.end;
  }
  if (pos < code.length) {
    html += escapeHtml(code.slice(pos));
  }

  return (
    <span
      className="whitespace-pre-wrap"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
