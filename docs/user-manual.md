# MindAtlas User Manual

This manual provides detailed instructions for all features of the MindAtlas personal knowledge and experience management system.

## Table of Contents

1. [System Overview](#system-overview)
2. [Dashboard](#dashboard)
3. [Entry Management](#entry-management)
   - [Browse Entry List](#browse-entry-list)
   - [Create New Entry](#create-new-entry)
   - [Edit Entry](#edit-entry)
   - [Search Entries](#search-entries)
4. [Knowledge Graph](#knowledge-graph)
5. [Timeline](#timeline)
6. [AI Assistant](#ai-assistant)
7. [System Settings](#system-settings)
   - [Entry Type Management](#entry-type-management)
   - [Tag Management](#tag-management)
   - [AI Provider Configuration](#ai-provider-configuration)
   - [Assistant Tool Configuration](#assistant-tool-configuration)
   - [Assistant Skill Configuration](#assistant-skill-configuration)
8. [Internationalization](#internationalization)

---

## System Overview

MindAtlas is a personal knowledge and experience management system that helps you:

- **Record**: Create structured knowledge and experience entries
- **Connect**: Build relationship networks between entries
- **Search**: Quickly retrieve historical records
- **Analyze**: Visualize knowledge structure through graphs
- **Summarize**: Organize and review with AI assistant

### Core Concepts

| Concept | Description |
|---------|-------------|
| **Entry** | Core entity representing a knowledge or experience record |
| **Type** | Entry classification (Knowledge, Project, Competition, etc.) |
| **Tag** | Flexible labeling system for multi-dimensional categorization |
| **Relation** | Connections between Entries (belongs_to, uses, related_to, etc.) |
| **Attachment** | Files associated with an Entry |

---

## Dashboard

**Path**: `/dashboard`

The dashboard is the system homepage, providing data overview:

- **Statistics Cards**: Display Entry count, type distribution, tag usage
- **Recent Activity**: Show recently created or modified Entries
- **Quick Actions**: Shortcuts to create new Entries

---

## Entry Management

**Path**: `/entries`

Entry is the core feature for recording and managing your knowledge and experiences.

### Browse Entry List

1. Click "Entries" in the sidebar to enter the list page
2. The list displays all Entries with title, type, tags, and creation time
3. Click any Entry to view details

**Filtering**:
- Filter by type: Select a specific type
- Filter by tags: Select one or more tags
- Keyword search: Enter keywords in the search box

### Create New Entry

1. Click the "New" button in the top right corner
2. Fill in Entry information:

| Field | Description | Required |
|-------|-------------|----------|
| Title | Entry name | Yes |
| Type | Select a preset type | Yes |
| Content | Supports Markdown format | No |
| Tags | Select or create tags | No |
| Time Mode | NONE/POINT/RANGE | No |

3. Click "Save" to complete creation

### Edit Entry

1. Click "Edit" button on the detail page
2. Modify the fields you want to update
3. Click "Save" to submit changes

**Managing Relations**:
- Add connections to other Entries in the "Relations" section
- Supported relation types: belongs_to, uses, related_to, etc.

**Managing Attachments**:
- Upload files in the "Attachments" section
- Supports drag-and-drop or click to select files
- Files are stored in MinIO object storage

### Search Entries

1. Use the search box at the top of the list page
2. Press Enter or click the search icon after entering keywords
3. Search scope includes title and content

---

## Knowledge Graph

**Path**: `/graph`

The knowledge graph visualizes the connection network between Entries.

### Basic Operations

- **Zoom**: Scroll mouse wheel to zoom in/out
- **Pan**: Hold and drag on empty area to move canvas
- **Select Node**: Click a node to view Entry details
- **Move Node**: Drag nodes to adjust positions

### Graph Elements

| Element | Description |
|---------|-------------|
| Node | Represents an Entry, color indicates type |
| Edge | Represents relation between Entries |
| Label | Displays Entry title |

---

## Timeline

**Path**: `/timeline`

Timeline displays Entries in chronological order, ideal for reviewing experiences and tracking progress.

### Viewing

- Entries arranged by creation time or event time
- Supports grouping by year/month
- Click Entry card to view details

---

## AI Assistant

**Path**: `/assistant`

The AI assistant is built on LangChain, supporting natural language interaction and tool calling.

### Basic Conversation

1. Click "Assistant" in the sidebar to enter the chat page
2. Enter your question or instruction in the input box
3. AI will automatically select appropriate skills based on intent

### Built-in Skills

| Skill | Description | Example |
|-------|-------------|---------|
| Search Records | Search Entries | "Find my notes about React" |
| View Details | Get Entry details | "Show me the details of this record" |
| Quick Stats | Get system statistics | "How many records do I have?" |
| Smart Capture | Quickly create Entry | "Record what I learned about Docker today" |
| Knowledge Synthesis | Organize knowledge | "Summarize my notes on microservices" |

### Conversation Management

- **New Conversation**: Click "New Chat" button on the left
- **History**: Left panel shows conversation history
- **Delete**: Hover over a conversation and click delete icon

### Prerequisites

Configure an AI provider in settings before using the AI assistant.

---

## System Settings

**Path**: `/settings`

The settings page provides access to various configuration options.

### Entry Type Management

**Path**: `/settings/entry-types`

Manage Entry classification types.

**Steps**:
1. Click "New Type" to add a new type
2. Enter type name, select icon and color
3. Click save to complete

**Edit/Delete**:
- Click type card to edit
- Ensure no Entries use the type before deleting

### Tag Management

**Path**: `/settings/tags`

Manage system tags.

**Steps**:
1. Click "New Tag"
2. Enter tag name
3. Click save

**Batch Operations**:
- Merge duplicate tags
- Batch delete unused tags

### AI Provider Configuration

**Path**: `/settings/ai-providers`

Configure AI providers to enable AI features.

**Steps**:
1. Click "Add Provider"
2. Fill in configuration:

| Field | Description |
|-------|-------------|
| Name | Provider display name |
| API Key | Key provided by the service |
| Base URL | API address (OpenAI-compatible) |
| Model | Model name to use |

3. Click save, API Key will be encrypted
4. Click "Activate" to enable the provider

### Assistant Tool Configuration

**Path**: `/settings/assistant-tools`

Configure tools available to the AI assistant.

**Built-in Tools**:
- search_entries - Search Entries
- get_entry_detail - Get Entry details
- create_entry - Create Entry
- get_statistics - Get statistics
- list_entry_types - List types
- list_tags - List tags

**Custom Tools**:
Add remote APIs as tools by configuring HTTP endpoints and parameters.

### Assistant Skill Configuration

**Path**: `/settings/assistant-skills`

Skills are predefined workflows that AI automatically selects based on user intent.

**Skill Modes**:
- **Agent Mode**: AI autonomously decides which tools to call
- **Pipeline Mode**: Execute steps in predefined order

**Custom Skills**:
1. Click "New Skill"
2. Configure name, description, intent examples
3. Select available tools
4. Set execution steps (Pipeline mode)

---

## Internationalization

The system supports Chinese and English interfaces.

### Switch Language

1. Click the language switch button in the top right corner
2. Select "中文" or "English"
3. Interface switches immediately

Language preference is saved automatically.

---

## FAQ

### AI Assistant Not Responding

1. Check if AI provider is configured and activated
2. Verify API Key is valid and has balance
3. Check network connection

### Attachment Upload Failed

1. Check if MinIO service is running
2. Confirm file size is within limits
3. Check bucket permission configuration

### Graph Shows Blank

1. Confirm Entries exist with relations
2. Refresh page to reload data
