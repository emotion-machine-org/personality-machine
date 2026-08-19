import { useEffect, useRef, useState } from 'react';

interface UseIntersectionObserverOptions {
  threshold?: number | number[];
  root?: Element | null;
  rootMargin?: string;
  enabled?: boolean;
}

/**
 * Hook for observing intersection with a target element.
 * Used for infinite scroll functionality.
 *
 * @param callback - Function to call when target becomes visible
 * @param options - IntersectionObserver options + enabled flag
 * @returns ref to attach to the target element
 */
export function useIntersectionObserver(
  callback: () => void,
  options: UseIntersectionObserverOptions = {}
) {
  const { threshold = 0, root = null, rootMargin = '0px', enabled = true } = options;
  const targetRef = useRef<HTMLDivElement | null>(null);
  const callbackRef = useRef(callback);

  // Keep callback ref updated
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) return;

    const target = targetRef.current;
    if (!target) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          callbackRef.current();
        }
      },
      { threshold, root, rootMargin }
    );

    observer.observe(target);

    return () => {
      observer.disconnect();
    };
  }, [threshold, root, rootMargin, enabled]);

  return targetRef;
}

/**
 * Hook that returns both visibility state and ref.
 * Useful when you need to know if an element is visible.
 */
export function useIntersectionVisible(options: UseIntersectionObserverOptions = {}) {
  const [isVisible, setIsVisible] = useState(false);
  const targetRef = useRef<HTMLDivElement | null>(null);

  const { threshold = 0, root = null, rootMargin = '0px', enabled = true } = options;

  useEffect(() => {
    if (!enabled) {
      setIsVisible(false);
      return;
    }

    const target = targetRef.current;
    if (!target) return;

    const observer = new IntersectionObserver(
      (entries) => {
        setIsVisible(entries[0]?.isIntersecting ?? false);
      },
      { threshold, root, rootMargin }
    );

    observer.observe(target);

    return () => {
      observer.disconnect();
    };
  }, [threshold, root, rootMargin, enabled]);

  return { ref: targetRef, isVisible };
}
