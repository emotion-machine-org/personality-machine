'use client';

import { useState } from 'react';
import Icon from '@/components/ui/icon';

export interface ChatImage {
  id: string;
  url: string;
  description?: string;
  mime_type?: string;
  width?: number;
  height?: number;
  isUploading?: boolean;
  error?: string;
}

interface ChatImageAttachmentProps {
  image: ChatImage;
  size?: 'sm' | 'md' | 'lg';
  showDescription?: boolean;
  className?: string;
}

const sizeClasses = {
  sm: 'w-[72px] h-[72px]',
  md: 'max-w-[200px] max-h-[200px]',
  lg: 'max-w-[300px] max-h-[300px]',
};

export function ChatImageAttachment({
  image,
  size = 'md',
  showDescription = false,
  className = '',
}: ChatImageAttachmentProps) {
  const [imageError, setImageError] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);

  if (image.error || imageError) {
    return (
      <div
        className={`flex items-center justify-center rounded-lg bg-[#1f1f1f] text-white/40 ${sizeClasses[size]} ${className}`}
      >
        <Icon name="x" size={16} />
      </div>
    );
  }

  return (
    <div className={`relative ${sizeClasses[size]} ${className}`}>
      {/* Background container - always visible */}
      <div className={`absolute inset-0 rounded-lg bg-[#1f1f1f] overflow-hidden`}>
        {/* Image */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={image.url}
          alt={image.description || 'Uploaded image'}
          className={`w-full h-full object-cover ${isLoaded ? 'opacity-100' : 'opacity-0'} transition-opacity duration-200`}
          onLoad={() => setIsLoaded(true)}
          onError={() => setImageError(true)}
        />
      </div>

      {/* Loading spinner overlay - shown during upload OR while image loads */}
      {(image.isUploading || !isLoaded) && (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-[#1f1f1f] z-10">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-white/20 border-t-white" />
        </div>
      )}

      {showDescription && image.description && isLoaded && (
        <p className="mt-1 text-xs text-white/50 line-clamp-2">{image.description}</p>
      )}
    </div>
  );
}

interface ChatImageGridProps {
  images: ChatImage[];
  size?: 'sm' | 'md' | 'lg';
  align?: 'left' | 'right';
  className?: string;
}

export function ChatImageGrid({ images, size = 'md', align = 'left', className = '' }: ChatImageGridProps) {
  if (!images || images.length === 0) return null;

  const justifyClass = align === 'right' ? 'justify-end' : 'justify-start';

  if (images.length === 1) {
    return (
      <div className={`flex ${justifyClass} ${className}`}>
        <ChatImageAttachment image={images[0]} size={size} />
      </div>
    );
  }

  return (
    <div className={`flex flex-wrap gap-2 ${justifyClass} ${className}`}>
      {images.map((image) => (
        <ChatImageAttachment key={image.id} image={image} size="sm" />
      ))}
    </div>
  );
}

interface StagedImageProps {
  image: ChatImage;
  onRemove: (id: string) => void;
}

export function StagedImage({ image, onRemove }: StagedImageProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className="relative group"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <ChatImageAttachment image={image} size="sm" />
      {(isHovered || image.error) && !image.isUploading && (
        <button
          type="button"
          onClick={() => onRemove(image.id)}
          className="absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-[#3C3C3C] text-white hover:bg-white hover:text-black transition"
          aria-label="Remove image"
        >
          <Icon name="x" size={10} />
        </button>
      )}
      {image.error && (
        <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/70">
          <span className="text-xs text-red-400">Failed</span>
        </div>
      )}
    </div>
  );
}

interface StagedImagesPreviewProps {
  images: ChatImage[];
  onRemove: (id: string) => void;
  className?: string;
}

export function StagedImagesPreview({ images, onRemove, className = '' }: StagedImagesPreviewProps) {
  if (!images || images.length === 0) return null;

  return (
    <div className={`flex flex-wrap gap-2 p-3 ${className}`}>
      {images.map((image) => (
        <StagedImage key={image.id} image={image} onRemove={onRemove} />
      ))}
    </div>
  );
}

interface ImageUploadButtonProps {
  onSelect: (files: FileList) => void;
  disabled?: boolean;
  className?: string;
  accept?: string;
}

export function ImageUploadButton({
  onSelect,
  disabled = false,
  className = '',
  accept = 'image/jpeg,image/png,image/webp,image/gif',
}: ImageUploadButtonProps) {
  const handleClick = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.multiple = true;
    input.onchange = (e) => {
      const files = (e.target as HTMLInputElement).files;
      if (files && files.length > 0) {
        onSelect(files);
      }
    };
    input.click();
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      className={`flex h-12 w-12 items-center justify-center rounded-full bg-[#1a1a1a] text-white/60 transition hover:bg-[#2a2a2a] hover:text-white disabled:opacity-40 disabled:cursor-not-allowed ${className}`}
      aria-label="Attach image"
      title="Attach image"
    >
      <Icon name="image" size={18} />
    </button>
  );
}
