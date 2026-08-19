/**
 * Hook for managing image uploads in chat conversations.
 * Handles file upload to the server, staging images before sending, and error states.
 */

import { useState, useCallback, useRef } from 'react';
import { useAuth } from '@clerk/nextjs';
import type { ChatImage } from '@/components/ui/chat-image';
import { API_CONFIG } from '@/lib/config';

const API_BASE = API_CONFIG.BASE_URL;

const MAX_FILE_SIZE_MB = 10;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];

interface UseImageUploadResult {
  /** Images staged for the current message (uploaded but not yet sent with a message) */
  stagedImages: ChatImage[];
  /** Whether any images are currently uploading */
  isUploading: boolean;
  /** Upload one or more image files */
  uploadImages: (conversationId: string, files: FileList) => Promise<void>;
  /** Remove a staged image by ID */
  removeStagedImage: (imageId: string) => void;
  /** Clear all staged images (e.g., after sending message) */
  clearStagedImages: () => void;
  /** Get image IDs for sending with a message */
  getStagedImageIds: () => string[];
  /** Any upload error message */
  uploadError: string | null;
  /** Clear upload error */
  clearUploadError: () => void;
}

export function useImageUpload(): UseImageUploadResult {
  const [stagedImages, setStagedImages] = useState<ChatImage[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const { getToken } = useAuth();
  const uploadCountRef = useRef(0);

  const validateFile = useCallback((file: File): string | null => {
    if (!ALLOWED_TYPES.includes(file.type)) {
      return `Invalid file type: ${file.type}. Allowed: JPEG, PNG, WebP, GIF`;
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return `File too large: ${(file.size / 1024 / 1024).toFixed(1)}MB. Max: ${MAX_FILE_SIZE_MB}MB`;
    }
    return null;
  }, []);

  const uploadImages = useCallback(async (conversationId: string, files: FileList) => {
    if (!conversationId || files.length === 0) return;

    setUploadError(null);

    const filesToUpload = Array.from(files);
    const validationErrors: string[] = [];

    // Validate all files first
    for (const file of filesToUpload) {
      const error = validateFile(file);
      if (error) {
        validationErrors.push(`${file.name}: ${error}`);
      }
    }

    if (validationErrors.length === filesToUpload.length) {
      // All files failed validation
      setUploadError(validationErrors.join('\n'));
      return;
    }

    // Filter to valid files only
    const validFiles = filesToUpload.filter(f => !validateFile(f));

    // Create placeholder entries for optimistic UI
    const placeholders: ChatImage[] = validFiles.map((file, idx) => ({
      id: `uploading-${Date.now()}-${idx}`,
      url: URL.createObjectURL(file),
      isUploading: true,
    }));

    setStagedImages(prev => [...prev, ...placeholders]);
    uploadCountRef.current += validFiles.length;
    setIsUploading(true);

    const token = await getToken(
      process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE
        ? { template: process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE }
        : undefined
    );

    // Upload each file
    const uploadPromises = validFiles.map(async (file, idx) => {
      const placeholder = placeholders[idx];
      const formData = new FormData();
      formData.append('file', file);

      try {
        const response = await fetch(
          `${API_BASE}/conversations/${conversationId}/images`,
          {
            method: 'POST',
            headers: {
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: formData,
          }
        );

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || `Upload failed: ${response.statusText}`);
        }

        const data = await response.json();

        // Replace placeholder with real image data
        setStagedImages(prev =>
          prev.map(img =>
            img.id === placeholder.id
              ? {
                  id: data.image_id,
                  url: data.storage_url,
                  description: data.description,
                  mime_type: data.mime_type,
                  width: data.width,
                  height: data.height,
                  isUploading: false,
                }
              : img
          )
        );

        // Revoke the blob URL to free memory
        URL.revokeObjectURL(placeholder.url);

        return { success: true };
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Upload failed';

        // Mark placeholder as failed
        setStagedImages(prev =>
          prev.map(img =>
            img.id === placeholder.id
              ? { ...img, isUploading: false, error: errorMessage }
              : img
          )
        );

        return { success: false, error: errorMessage };
      } finally {
        uploadCountRef.current--;
        if (uploadCountRef.current === 0) {
          setIsUploading(false);
        }
      }
    });

    const results = await Promise.all(uploadPromises);
    const failures = results.filter(r => !r.success);

    if (failures.length > 0 && validationErrors.length > 0) {
      setUploadError([...validationErrors, ...failures.map(f => f.error)].join('\n'));
    } else if (validationErrors.length > 0) {
      setUploadError(validationErrors.join('\n'));
    }
  }, [getToken, validateFile]);

  const removeStagedImage = useCallback((imageId: string) => {
    setStagedImages(prev => {
      const img = prev.find(i => i.id === imageId);
      // Revoke blob URL if it's a local preview
      if (img?.url.startsWith('blob:')) {
        URL.revokeObjectURL(img.url);
      }
      return prev.filter(i => i.id !== imageId);
    });
  }, []);

  const clearStagedImages = useCallback(() => {
    setStagedImages(prev => {
      // Revoke all blob URLs
      prev.forEach(img => {
        if (img.url.startsWith('blob:')) {
          URL.revokeObjectURL(img.url);
        }
      });
      return [];
    });
  }, []);

  const getStagedImageIds = useCallback(() => {
    return stagedImages
      .filter(img => !img.isUploading && !img.error && !img.id.startsWith('uploading-'))
      .map(img => img.id);
  }, [stagedImages]);

  const clearUploadError = useCallback(() => {
    setUploadError(null);
  }, []);

  return {
    stagedImages,
    isUploading,
    uploadImages,
    removeStagedImage,
    clearStagedImages,
    getStagedImageIds,
    uploadError,
    clearUploadError,
  };
}
