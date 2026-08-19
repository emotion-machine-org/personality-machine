// Types based on the system design document
export interface User {
    id: string;
    clerk_user_id: string;
    email: string;
    username: string;
    display_name: string;
    avatar_url: string | null;
    auth_provider: string;
    created_at: string;
    updated_at: string;
    onboarding_completed: boolean;
    onboarding_completed_at: string | null;
  }

  export interface Companion {
    id: string;
    name: string;
    last_updated: string;
  }

  export interface InferenceConfig {
    model: string | null;
    temperature: number;
    max_output_tokens: number | null;
  }

  export interface VoiceConfig {
    preset?: string | null;
    stt_provider?: string | null;
    tts_provider?: string | null;
    voice_name?: string | null;
  }

  export interface KnowledgeConfig {
    enabled?: boolean;
    gate_strategy?: string;
    top_k?: number;
    min_confidence?: number;
    classifier_summary?: string | null;
  }

  export interface IntroMessageConfig {
    enabled?: boolean;
    text?: string;
    send_once_per_relationship?: boolean;
  }

  export interface CompanionConfig {
    system_prompt: {
      full_system_prompt: string;
      identity: string;
      personality: string;
      style: string;
      backstory: string;
      additional_instructions: string;
      self_image: string;
    };
    memory: {
      enabled: boolean;
      version?: 1 | 2;
      core_memories: string[];
      memory_evaluation_prompt: string;
      recency: number;
      top_k: number;
      min_saliency: number;
      // V2-specific fields
      max_entries?: number;
      model?: string | null;
      ingestion_prompt?: string | null;
    };
    classifier?: {
      enabled?: boolean;
      model?: string | null;
      instructions?: string | null;
    };
    knowledge?: KnowledgeConfig;
    inference?: InferenceConfig;
    voice?: VoiceConfig;
    intro_message?: IntroMessageConfig;
    context_mode?: 'legacy' | 'layered' | 'raw';
    layers?: LayerAttachment[];
    context?: ContextSettings;

    // Profile schema template for user profiles
    profile_schema?: Record<string, unknown>;
  }

  export interface LayerAttachment {
    key: string;
    category: string;
    enabled: boolean;
    priority: number;
    params: Record<string, unknown>;
    timeout_ms?: number | null;
    reserved_tokens?: number | null;
    depends_on: string[];
  }

  export interface ContextSettings {
    max_prompt_tokens?: number | null;
    target_prompt_fraction: number;
    reserved_completion_tokens?: number | null;
    tool_summary?: string | null;
    message_limit?: number;
  }

  export interface CompanionVersionSummary {
    id: string;
    version_number: number;
    created_at: string;
    config: CompanionConfig;
  }

  export interface CompanionVersionDetail extends CompanionVersionSummary {
    companion_id: string;
  }

  export interface SessionCreate {
    systemPrompt: string;
    voice: string;
  }

  export interface SessionCreated {
    id: string;
    ws_url: string;
  }

  export interface WebSocketTicket {
    ticket: string;
    expires_in: number;
  }

  // Analytics types
  export interface ConversationSummary {
    id: string;
    external_user_id: string;
    started_at: string;
    message_count: number;
    last_message_at: string | null;
  }

  export interface AnalyticsMessage {
    id: string;
    role: string;
    content: string;
    created_at: string;
    input_modality?: 'voice' | 'text' | null;
  }

  export interface SystemPromptResponse {
    system_prompt: string;
  }

  // Conversation labeling
  export interface ConversationLabel {
    conversation_id: string;
    external_user_id: string | null;
    started_at: string;
    last_message_at: string | null;
    message_count: number;
    engagement_label: 'not_engaged' | 'engaged' | 'very_engaged' | null;
    dependency_risk_label: 'no_risk' | 'some_risk' | null;
    analyzed_at: string | null;
  }

  // Voice mapping types
  export interface VoiceMapping {
    name: string;
    id: string;
  }

export interface VoiceMappings {
    openai: string[];
    elevenlabs: VoiceMapping[];
    cartesia: VoiceMapping[];
  }

  // Memory types
  export interface MemoryItem {
    id: string;
    companion_id: string;
    content: string;
    created_at: string;
    importance: number;
    weight_user: number;
    modality: string;
    last_accessed_at: string;
    commentary?: string | null;
    conversation_id?: string | null;
    sender_type: string;
    external_user_id?: string | null;
    message_id?: string | null;
    is_core: boolean;
    similarity?: number | null;
    score?: number | null;
    pending?: boolean;
  }

  export interface MemoryStats {
    total: number;
    avg_importance?: number | null;
    avg_weight_user?: number | null;
    by_sender_type: Record<string, number>;
    by_modality: Record<string, number>;
  }

  export interface MemoryListResponse {
    items: MemoryItem[];
    total_count: number;
  }

  export interface UserMemoriesPayload {
    items: MemoryItem[];
    total: number;
  }

  export interface UserAnalyticsSummary {
    external_user_id: string;
    session_count: number;
    total_message_count: number;
  }

  export interface CompanionShare {
    id: string;
    companion_id: string;
    slug: string;
    status: 'draft' | 'active' | 'disabled';
    allow_text: boolean;
    allow_voice: boolean;
    require_auth: boolean;
    expose_status_events: boolean;
    display_name?: string | null;
    // Context copy surfaced on the public companion experience.
    description?: string | null;
    version_id?: string | null;
    config_snapshot?: Record<string, unknown> | null;
    created_at: string;
    updated_at: string;
    activated_at?: string | null;
    disabled_at?: string | null;
    total_sessions: number;
    total_messages: number;
    total_voice_sessions: number;
    last_activity_at?: string | null;
    has_pending_changes: boolean;
  }

  export interface CompanionShareAnalytics {
    share_id: string;
    sessions: number;
    total_messages: number;
    total_voice_sessions: number;
    last_activity_at?: string | null;
  }

  export interface PublicShareMeta {
    slug: string;
    display_name?: string | null;
    // Context copy surfaced on the public companion experience.
    description?: string | null;
    allow_text: boolean;
    allow_voice: boolean;
    require_auth: boolean;
    expose_status_events: boolean;
  }

  // API Key types
  export interface ApiKey {
    id: string;
    project_id: string;
    created_by: string | null;
    name: string | null;
    prefix: string;
    status: 'active' | 'revoked';
    scopes: string[];
    metadata: Record<string, unknown>;
    created_at: string;
    last_used_at: string | null;
    expires_at: string | null;
  }

  export interface ApiKeyWithSecret extends ApiKey {
    secret: string;
  }

  // Relationship types (dashboard)
  export interface RelationshipSummary {
    id: string;
    user_id: string;
    message_count: number;
    last_interaction_at: string | null;
    created_at: string;
    profile: Record<string, unknown>;
  }

  export interface RelationshipListResponse {
    items: RelationshipSummary[];
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
  }

  // Project Secret types
  export interface ProjectSecret {
    id: string;
    project_id: string;
    secret_name: string;
    description: string | null;
    created_at: string;
    updated_at: string;
  }
