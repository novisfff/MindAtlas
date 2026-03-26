import type {
  RuntimeDocumentParsingConfigResponse,
  RuntimeKnowledgeGraphConfigResponse,
  SecretFieldState,
} from './api/runtime-config'
import type { Locale } from '@/stores/app-store'

interface RuntimeValidationMessageFactory {
  fieldLabel: (key: string) => string
  completeField: (field: string) => string
}

type KnowledgeGraphValidationDraft = Pick<
  RuntimeKnowledgeGraphConfigResponse,
  'enabled' | 'embeddingModelId' | 'embeddingModelName' | 'embeddingHost' | 'rerankModel' | 'rerankHost'
> & {
  embeddingApiKey: string
  embeddingApiKeyState: SecretFieldState
  rerankApiKey: string
  rerankApiKeyState: SecretFieldState
}

type DocumentParsingValidationDraft = Pick<
  RuntimeDocumentParsingConfigResponse,
  | 'workerEnabled'
  | 'ocrEnabled'
  | 'ocrLangs'
  | 'pictureDescriptionEnabled'
  | 'pictureDescriptionUrl'
  | 'pictureDescriptionModel'
  | 'pictureDescriptionPrompt'
> & {
  pictureDescriptionApiKey: string
  pictureDescriptionApiKeyState: SecretFieldState
}

export function hasConfiguredSecret(value: string | null | undefined, configured?: boolean) {
  return Boolean((value ?? '').trim() || configured)
}

export function getDefaultLightRagSummaryLanguage(locale: Locale) {
  return locale === 'zh' ? 'Chinese' : 'English'
}

export function isLightRagSummaryLanguageLocked(initialized: boolean, summaryLanguage: string | null | undefined) {
  return initialized && Boolean((summaryLanguage ?? '').trim())
}

export function isLightRagEmbeddingModelLocked(
  initialized: boolean,
  embeddingModelId?: string | null,
  embeddingModelName?: string | null
) {
  return initialized
}

export function isLightRagEmbeddingHostLocked(initialized: boolean, embeddingHost?: string | null) {
  return initialized && Boolean((embeddingHost ?? '').trim())
}

export function isKnowledgeGraphRerankEnabled(
  draft: Pick<KnowledgeGraphValidationDraft, 'rerankModel' | 'rerankHost' | 'rerankApiKey' | 'rerankApiKeyState'>
) {
  return Boolean(
    draft.rerankModel.trim() ||
      draft.rerankHost.trim() ||
      draft.rerankApiKey.trim() ||
      draft.rerankApiKeyState.configured
  )
}

export function validateKnowledgeGraphCapability(
  draft: KnowledgeGraphValidationDraft,
  messages: RuntimeValidationMessageFactory,
  rerankEnabled = isKnowledgeGraphRerankEnabled(draft)
) {
  if (!draft.enabled) {
    return null
  }

  if (!((draft.embeddingModelName ?? '').trim() || draft.embeddingModelId)) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.knowledgeGraph.embeddingModelName.label')
    )
  }

  if (!draft.embeddingHost.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.knowledgeGraph.embeddingHost.label')
    )
  }

  if (!hasConfiguredSecret(draft.embeddingApiKey, draft.embeddingApiKeyState.configured)) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.knowledgeGraph.embeddingApiKey.label')
    )
  }

  if (!rerankEnabled) {
    return null
  }

  if (!draft.rerankModel.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.knowledgeGraph.rerankModel.label')
    )
  }

  if (!draft.rerankHost.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.knowledgeGraph.rerankHost.label')
    )
  }

  if (!hasConfiguredSecret(draft.rerankApiKey, draft.rerankApiKeyState.configured)) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.knowledgeGraph.rerankApiKey.label')
    )
  }

  return null
}

export function validateDocumentParsingCapability(
  draft: DocumentParsingValidationDraft,
  messages: RuntimeValidationMessageFactory
) {
  if (!draft.workerEnabled) {
    return null
  }

  if (draft.ocrEnabled && !draft.ocrLangs.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.documentParsing.ocrLangs.label')
    )
  }

  if (!draft.pictureDescriptionEnabled) {
    return null
  }

  if (!draft.pictureDescriptionUrl.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.documentParsing.pictureDescriptionUrl.label')
    )
  }

  if (!draft.pictureDescriptionModel.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.documentParsing.pictureDescriptionModel.label')
    )
  }

  if (!hasConfiguredSecret(draft.pictureDescriptionApiKey, draft.pictureDescriptionApiKeyState.configured)) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.documentParsing.pictureDescriptionApiKey.label')
    )
  }

  if (!draft.pictureDescriptionPrompt.trim()) {
    return messages.completeField(
      messages.fieldLabel('systemSetup.forms.documentParsing.pictureDescriptionPrompt.label')
    )
  }

  return null
}
