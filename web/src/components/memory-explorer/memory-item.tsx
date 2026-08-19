'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import Icon from '@/components/ui/icon';
import { TypeBadge, MEMORY_TYPES } from './type-badge';
import type { MemoryV2Entry } from '@/lib/memory-v2-api';

interface MemoryItemProps {
  entry: MemoryV2Entry;
  index: number;
  isNew?: boolean;
  isEditing?: boolean;
  onEditStart?: () => void;
  onEditEnd?: () => void;
  onUpdate: (entryId: string, content: string, type: string | null) => Promise<void>;
  onDelete: (entryId: string) => Promise<void>;
}

export function MemoryItem({
  entry,
  index,
  isNew = false,
  isEditing = false,
  onEditStart,
  onEditEnd,
  onUpdate,
  onDelete,
}: MemoryItemProps) {
  const [isHovered, setIsHovered] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Highlight new memories with a subtle border
  const highlightBorder = isNew ? 'border-l-2 border-[#85cd75]' : '';
  const [editContent, setEditContent] = useState(entry.content);
  const [editType, setEditType] = useState(entry.type);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // Reset edit content when entry changes or editing starts
  useEffect(() => {
    if (isEditing) {
      setEditContent(entry.content);
      setEditType(entry.type);
    }
  }, [isEditing, entry.content, entry.type]);

  const handleSave = useCallback(async () => {
    if (!editContent.trim()) return;
    setIsSaving(true);
    try {
      await onUpdate(entry.id, editContent.trim(), editType);
      onEditEnd?.();
    } catch (error) {
      console.error('Failed to update memory:', error);
    } finally {
      setIsSaving(false);
    }
  }, [entry.id, editContent, editType, onUpdate, onEditEnd]);

  const handleDelete = useCallback(async () => {
    setIsDeleting(true);
    try {
      await onDelete(entry.id);
    } catch (error) {
      console.error('Failed to delete memory:', error);
      setIsDeleting(false);
    }
  }, [entry.id, onDelete]);

  const handleCancel = useCallback(() => {
    setEditContent(entry.content);
    setEditType(entry.type);
    onEditEnd?.();
  }, [entry.content, entry.type, onEditEnd]);

  // Handle clicks outside container to exit edit mode
  useEffect(() => {
    if (!isEditing) return;

    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement;

      // If click is inside this container, let the container handle it
      if (containerRef.current?.contains(target)) {
        // But still check if it's on interactive elements
        if (
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.tagName === 'OPTION' ||
          target.tagName === 'BUTTON' ||
          target.closest('button')
        ) {
          return;
        }
      }

      // Click was outside container or on non-interactive area, close edit mode
      handleCancel();
    };

    // Use mousedown for faster response
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isEditing, handleCancel]);

  // Format index with leading zeros (001., 002., etc.)
  const formattedIndex = String(index + 1).padStart(3, '0') + '.';

  if (isDeleting) {
    return (
      <div className="flex flex-col items-start w-full opacity-50">
        <span className="text-[16px] text-white/60">Deleting...</span>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`flex flex-col items-start w-full relative ${highlightBorder} ${isNew ? 'pl-[8px]' : ''}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Header row: index + badge */}
      <div className="flex gap-[10px] items-start pb-[8px] w-full h-[23px]">
        <span
          className="text-[16px] leading-none tracking-[-0.64px] font-light"
          style={{ color: isHovered ? 'rgba(255,255,255,0.8)' : 'rgba(255,255,255,0.6)' }}
        >
          {formattedIndex}
        </span>

        {isEditing ? (
          <select
            value={editType || 'other'}
            onChange={(e) => setEditType(e.target.value === 'other' ? null : e.target.value)}
            className="bg-black text-white text-[12px] px-[4px] h-[17px] border-none outline-none"
          >
            {MEMORY_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        ) : (
          <TypeBadge type={entry.type} />
        )}

        {/* Trash icon - shown on hover, positioned to the left of content */}
        {isHovered && !isEditing && (
          <button
            onClick={handleDelete}
            className="absolute left-[-36px] top-[22px]"
            title="Delete"
          >
            <Icon name="trash" size={20} className="text-white" />
          </button>
        )}
      </div>

      {/* Content */}
      {isEditing ? (
        <div className="flex flex-col gap-[16px] w-full">
          <textarea
            ref={textareaRef}
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            className="w-full bg-black text-white text-[20px] leading-[1.1] tracking-[0.2px] p-[10px] font-book border-none outline-none resize-none"
            rows={3}
            autoFocus
          />
          <div className="flex items-center">
            <button
              onClick={handleCancel}
              disabled={isSaving}
              className="text-[20px] font-light text-white/40 hover:text-white transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={isSaving || !editContent.trim()}
              className="pl-3 text-[20px] font-light text-white/60 hover:text-white transition-colors disabled:opacity-50"
            >
              {isSaving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      ) : (
        <div
          className="flex items-center w-full cursor-pointer"
          onMouseDown={() => onEditStart?.()}
        >
          <p
            className="text-[20px] leading-[1.1] tracking-[0.2px] font-book"
            style={{ color: isHovered ? 'white' : 'rgba(255,255,255,0.8)' }}
          >
            {entry.content}
          </p>
        </div>
      )}
    </div>
  );
}
