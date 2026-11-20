/* PromptInput: a chat box that grows from a pill into a card when you start typing. Ported from the 21st.dev
   "ai-chat-input" to this app's design system (agent-trace.css's sibling, ai-chat-input.css): the spring
   animations, auto-height, scroll fades, attachments tray and shared-element preview are the original's.
   Changed for Bidwright: the microphone became an upload button (empty box: add files; typed: send). Files are
   any request document (PDF, email, text, Word, Excel, images), and the model picker is gone (the app always
   answers with Claude), while the effort switch sets how hard the assistant thinks. */
import * as React from "react";
import { useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { ArrowUp, FileText, Upload, X } from "lucide-react";
import { cn } from "@/lib/utils";
import "./ai-chat-input.css";

const SPRING = "cubic-bezier(0.175, 0.885, 0.32, 1.275)";
const SPRING_TRANSITION = `max-width 0.4s ${SPRING}, height 0.4s ${SPRING}`;
const SMOOTH_HEIGHT_TRANSITION = `max-width 0.4s ${SPRING}, height 0.15s ease-out`;
const MIN_TEXT = 68;
const MAX_TEXT = 160;

interface Attachment {
  id: string;
  file: File;
  url: string | null;  // object URL for images; other documents show a file tile
  name: string;
  width?: number;
  height?: number;
}

export interface PromptInputHandle {
  /** Open the box and put the cursor in it. */
  focus: () => void;
  /** Open the file chooser. */
  addFiles: () => void;
}

export interface PromptInputProps {
  onSubmit?: (value: string, meta: { effort: string; effortIndex: number; attachments: File[] }) => void;
  placeholder?: string;
  className?: string;
  /** Effort levels, shown with one to three bars. */
  efforts?: string[];
  defaultEffort?: number;
  defaultValue?: string;
  value?: string;
  onChange?: (value: string) => void;
  maxAttachments?: number;
  /** File types the upload button accepts. */
  accept?: string;
  /** The answer is being worked on: the box stays usable for typing, but cannot send. */
  busy?: boolean;
  /** Open on mount (e.g. the first screen of the app). */
  autoFocus?: boolean;
  id?: string;
  /** Width of the closed pill; the open card fills its container. */
  collapsedWidth?: number;
}

function EffortBars({ level, of }: { level: number; of: number }) {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
      <rect x="1.5" y="8" width="2.5" height="4.5" rx="1" fill="currentColor" />
      <rect x="5.75" y="5" width="2.5" height="7.5" rx="1" fill="currentColor" opacity={level >= 1 ? 1 : 0.3} className="bw-pi__bar" />
      <rect x="10" y="2" width="2.5" height="10.5" rx="1" fill="currentColor" opacity={level >= of - 1 ? 1 : 0.3} className="bw-pi__bar" />
    </svg>
  );
}

/** A label that animates its width when the word changes. */
function MorphingText({ text }: { text: string }) {
  const [width, setWidth] = useState<number | "auto">("auto");
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => { if (ref.current) setWidth(ref.current.offsetWidth); }, [text]);
  return (
    <span className="bw-pi__morph" style={{ width }}>
      <span ref={ref} className="bw-pi__morph-measure">{text}</span>
      <span key={text} className="bw-pi__morph-text">{text}</span>
    </span>
  );
}

function AttachmentThumb({ attachment, index, onRemove, onOpen }: {
  attachment: Attachment; index: number; onRemove: (id: string) => void; onOpen: (a: Attachment, rect: DOMRect) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const ext = attachment.name.split(".").pop()?.toUpperCase().slice(0, 4) ?? "";
  return (
    <div className="bw-pi__thumb" style={{ animationDelay: `${index * 35}ms` }}>
      <button
        ref={ref}
        type="button"
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          e.stopPropagation();
          if (attachment.url && ref.current) onOpen(attachment, ref.current.getBoundingClientRect());
        }}
        className="bw-pi__thumb-btn"
        aria-label={attachment.url ? `Open preview of ${attachment.name}` : attachment.name}
        title={attachment.name}
      >
        {attachment.url
          ? <img src={attachment.url} alt="" draggable={false} />
          : <span className="bw-pi__file"><FileText aria-hidden="true" /><span>{ext}</span></span>}
      </button>
      <button type="button" className="bw-pi__thumb-remove" aria-label={`Remove ${attachment.name}`}
              onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); }}
              onClick={(e) => { e.stopPropagation(); onRemove(attachment.id); }}>
        <X aria-hidden="true" />
      </button>
    </div>
  );
}

/** The image grows from its thumbnail into the middle of the screen, and back. */
function AttachmentGalleryModal({ attachment, originRect, onClose }: { attachment: Attachment; originRect: DOMRect; onClose: () => void }) {
  const [phase, setPhase] = useState<"opening" | "open" | "closing">("opening");
  const [target, setTarget] = useState<{ top: number; left: number; width: number; height: number; radius: number } | null>(null);

  useEffect(() => {
    const maxW = Math.min(window.innerWidth * 0.86, 560);
    const maxH = Math.min(window.innerHeight * 0.78, 720);
    const w0 = attachment.width || 800, h0 = attachment.height || 600;
    const scale = Math.min(maxW / w0, maxH / h0, 1.6);
    const width = w0 * scale, height = h0 * scale;
    setTarget({ top: (window.innerHeight - height) / 2, left: (window.innerWidth - width) / 2, width, height, radius: 20 });
    const raf = requestAnimationFrame(() => setPhase("open"));
    return () => cancelAnimationFrame(raf);
  }, [attachment]);

  const close = useCallback(() => setPhase("closing"), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);

  const open = phase === "open";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const g = open && target ? target
    : { top: originRect.top, left: originRect.left, width: originRect.width, height: originRect.height, radius: 12 };
  const ease = phase === "closing" ? "ease-out" : SPRING;
  const dur = reduce ? "0.01s" : phase === "closing" ? "0.3s" : "0.45s";
  const transition = ["top", "left", "width", "height", "border-radius"].map((p) => `${p} ${dur} ${ease}`).join(", ");

  return (
    <div className="bw-pi-gallery" onClick={close} role="dialog" aria-modal="true" aria-label={attachment.name}>
      <div className="bw-pi-gallery__scrim" style={{ opacity: open ? 1 : 0 }} />
      <div className="bw-pi-gallery__image" data-open={open || undefined}
           style={{ top: g.top, left: g.left, width: g.width, height: g.height, borderRadius: g.radius, transition }}
           onTransitionEnd={() => { if (phase === "closing") onClose(); }} onClick={(e) => e.stopPropagation()}>
        <img src={attachment.url ?? ""} alt={attachment.name} draggable={false} />
      </div>
      <button type="button" onClick={close} aria-label="Close preview" className="bw-pi-gallery__close"
              style={{ opacity: open ? 1 : 0, transform: open ? "scale(1)" : "scale(0.7)" }}>
        <X aria-hidden="true" />
      </button>
    </div>
  );
}

export const PromptInput = React.forwardRef<PromptInputHandle, PromptInputProps>(function PromptInput({
  onSubmit, placeholder = "Ask anything", className, efforts = ["Quick", "Balanced", "Thorough"], defaultEffort = 0,
  defaultValue = "", value: controlledValue, onChange, maxAttachments = 6,
  accept = ".pdf,.eml,.msg,.txt,.docx,.xlsx,image/*", busy = false, autoFocus = false, id = "prompt", collapsedWidth = 420,
}, ref) {
  const [expanded, setExpanded] = useState(autoFocus);
  const [smooth, setSmooth] = useState(false);
  const [localValue, setLocalValue] = useState(defaultValue);
  const [effortIndex, setEffortIndex] = useState(defaultEffort);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [active, setActive] = useState<{ attachment: Attachment; rect: DOMRect } | null>(null);
  const [containerHeight, setContainerHeight] = useState(MIN_TEXT + 48);
  const [textHeight, setTextHeight] = useState(MIN_TEXT);
  const [scrolling, setScrolling] = useState(false);

  const controlled = controlledValue !== undefined;
  const value = controlled ? controlledValue : localValue;
  const hasAttachments = attachments.length > 0;
  const hasValue = value.trim() !== "" || hasAttachments;

  const textRef = useRef<HTMLTextAreaElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const topFade = useRef<HTMLDivElement>(null);
  const bottomFade = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;

  useImperativeHandle(ref, () => ({
    focus: () => { setSmooth(false); setExpanded(true); setTimeout(() => textRef.current?.focus(), 60); },
    addFiles: () => fileRef.current?.click(),
  }), []);

  const updateFades = () => {
    const el = textRef.current;
    if (!el) return;
    if (topFade.current) topFade.current.style.opacity = String(Math.min(el.scrollTop / 20, 1));
    if (bottomFade.current) {
      const below = el.scrollHeight - el.clientHeight - el.scrollTop;
      bottomFade.current.style.opacity = String(Math.min(Math.max(below - 16, 0) / 10, 1));
    }
  };

  const setValue = useCallback((v: string) => {
    setSmooth(true);
    if (!controlled) setLocalValue(v);
    onChange?.(v);
  }, [controlled, onChange]);

  // object URLs are released when the box goes away
  useEffect(() => () => attachmentsRef.current.forEach((a) => a.url && URL.revokeObjectURL(a.url)), []);

  useEffect(() => {
    if (hasValue && !expanded) { setSmooth(false); setExpanded(true); }
  }, [hasValue, expanded]);

  useEffect(() => {
    if (!expanded) return;
    const t = setTimeout(() => {
      const el = textRef.current;
      if (el && document.activeElement !== el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
    }, 50);
    return () => clearTimeout(t);
  }, [expanded]);

  // the text area grows with what is typed (68 to 160px), then scrolls
  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    const current = el.style.height;
    el.style.transition = "none";
    el.style.height = "0px";
    const full = el.scrollHeight;
    el.style.height = current;
    void el.offsetHeight;
    el.style.transition = "";
    const h = Math.max(MIN_TEXT, Math.min(full, MAX_TEXT));
    el.style.height = `${h}px`;
    setTextHeight(h);
    setScrolling(full > MAX_TEXT);
    setTimeout(updateFades, 0);
  }, [value, expanded]);

  useEffect(() => {
    setContainerHeight(Math.max(MIN_TEXT + 48, textHeight + 48));
    setTimeout(updateFades, 0);
  }, [textHeight]);

  const collapseIfEmpty = () => {
    if (value.trim() === "" && !hasAttachments) { setSmooth(false); setExpanded(false); }
  };
  const onBlur = (e: React.FocusEvent<HTMLDivElement>) => {
    if (rootRef.current?.contains(e.relatedTarget as Node) || active) return;
    collapseIfEmpty();
  };

  const submit = () => {
    if (busy || (value.trim() === "" && !hasAttachments)) return;
    setSmooth(false);
    onSubmit?.(value, { effort: efforts[effortIndex], effortIndex, attachments: attachments.map((a) => a.file) });
    setValue("");
    attachments.forEach((a) => a.url && URL.revokeObjectURL(a.url));
    setAttachments([]);
  };

  const addAttachment = (file: File, url: string | null, width?: number, height?: number) => {
    const aid = `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`;
    setAttachments((prev) => [...prev, { id: aid, file, url, name: file.name, width, height }]);
  };
  const filesChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    const accepted = files.slice(0, Math.max(0, maxAttachments - attachments.length));
    if (!accepted.length) return;
    if (!expanded) { setSmooth(false); setExpanded(true); } else setSmooth(true);
    for (const file of accepted) {
      if (!file.type.startsWith("image/")) { addAttachment(file, null); continue; }
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => addAttachment(file, url, img.naturalWidth, img.naturalHeight);
      img.onerror = () => addAttachment(file, url, 800, 600);
      img.src = url;
    }
  };
  const remove = (aid: string) => {
    setSmooth(true);
    setAttachments((prev) => {
      const t = prev.find((a) => a.id === aid);
      if (t?.url) URL.revokeObjectURL(t.url);
      return prev.filter((a) => a.id !== aid);
    });
  };

  // the round button sends; it appears once there is something to send (files are added with the paperclip)
  const mode = busy ? "busy" : hasValue ? "send" : "upload";
  const onAction = (e: React.MouseEvent) => {
    e.preventDefault();
    if (mode === "send") submit();
    else if (mode === "upload") fileRef.current?.click();
  };

  return (
    <>
      <div ref={rootRef} onBlur={onBlur} className={cn("bw-pi", className)} data-expanded={expanded || undefined}
           style={{ maxWidth: expanded ? "100%" : collapsedWidth, transition: smooth ? "max-width 0.15s ease-out" : `max-width 0.4s ${SPRING}` }}>
        <input ref={fileRef} type="file" accept={accept} multiple onChange={filesChosen} hidden tabIndex={-1} aria-hidden="true" />

        {/* the tray of attachments slides up from behind the card */}
        <div aria-hidden={!hasAttachments} className="bw-pi__tray-wrap"
             style={{ height: hasAttachments && expanded ? 68 : 0, transition: smooth ? "height 0.15s ease-out" : `height 0.4s ${SPRING}` }}>
          <div className="bw-pi__tray"
               style={{ transform: hasAttachments && expanded ? "translateY(0)" : "translateY(100%)", opacity: hasAttachments && expanded ? 1 : 0,
                        transition: smooth ? "transform 0.15s ease-out, opacity 0.15s ease-out" : `transform 0.4s ${SPRING}, opacity 0.3s ease-out` }}>
            {attachments.map((a, i) => (
              <AttachmentThumb key={a.id} attachment={a} index={i} onRemove={remove} onOpen={(x, rect) => setActive({ attachment: x, rect })} />
            ))}
          </div>
        </div>

        <div className="bw-pi__card" data-expanded={expanded || undefined}
             onMouseDown={(e) => { if (expanded && e.target !== textRef.current) { e.preventDefault(); textRef.current?.focus(); } }}
             style={{ height: expanded ? containerHeight : 48, transition: smooth ? SMOOTH_HEIGHT_TRANSITION : SPRING_TRANSITION,
                      overflow: expanded ? "visible" : "hidden" }}>
          <label htmlFor={id} className="bw-sr-only">Message</label>
          <textarea
            ref={textRef} id={id} value={value} placeholder={placeholder} autoComplete="off" dir="auto"
            onChange={(e) => setValue(e.target.value)} onScroll={updateFades}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
              if (e.key === "Escape") collapseIfEmpty();
            }}
            className="bw-pi__text" data-scrolling={scrolling || undefined}
            style={{ transition: smooth ? "height 0.15s ease-out" : `opacity 0.3s ease-out, transform 0.3s ease-out, height 0.4s ${SPRING}` }}
          />
          <div ref={topFade} className="bw-pi__fade bw-pi__fade--top" />
          <div ref={bottomFade} className="bw-pi__fade bw-pi__fade--bottom"
               style={{ opacity: 0, top: `${textHeight - 32}px`, transition: smooth ? "top 0.15s ease-out" : `top 0.4s ${SPRING}` }} />

          <button type="button" className="bw-pi__closed" onClick={() => { setSmooth(false); setExpanded(true); }}
                  aria-label="Write a message" tabIndex={expanded ? -1 : 0}>
            {placeholder}
          </button>

          <div className="bw-pi__actions">
            <button type="button" className="bw-pi__tool" onMouseDown={(e) => e.preventDefault()}
                    onClick={(e) => { e.stopPropagation(); setEffortIndex((i) => (i + 1) % efforts.length); }}
                    aria-label={`How hard to think: ${efforts[effortIndex]}. Change`} title="How hard the assistant thinks on this answer">
              <EffortBars level={effortIndex} of={efforts.length} />
              <MorphingText text={efforts[effortIndex]} />
            </button>
          </div>

          <button type="button" className="bw-pi__action" data-mode={mode} onMouseDown={(e) => { e.preventDefault(); e.stopPropagation(); }}
                  onClick={onAction} disabled={busy}
                  aria-label={mode === "send" ? "Send" : mode === "busy" ? "Working on the answer" : "Add files"}
                  title={mode === "upload" ? "Add files: PDF, email, Word, Excel or a screenshot" : undefined}>
            <span className="bw-pi__icon" data-show={mode === "send" || undefined}><ArrowUp aria-hidden="true" /></span>
            <span className="bw-pi__icon" data-show={mode === "upload" || undefined}><Upload aria-hidden="true" /></span>
            <span className="bw-pi__icon bw-pi__busy" data-show={mode === "busy" || undefined} aria-hidden="true"><i /><i /><i /></span>
          </button>
        </div>
      </div>

      {active && <AttachmentGalleryModal attachment={active.attachment} originRect={active.rect} onClose={() => setActive(null)} />}
    </>
  );
});

export default PromptInput;
