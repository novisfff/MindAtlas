import { useRef } from 'react'
import { createStore, StoreApi } from 'zustand'
import { ChatStoreContext, createChatLogic, ChatState } from '../stores/chat-store'

export const ChatStoreProvider = ({ children }: { children: React.ReactNode }) => {
    const storeRef = useRef<StoreApi<ChatState>>()
    if (!storeRef.current) {
        storeRef.current = createStore<ChatState>(createChatLogic)
    }

    return (
        <ChatStoreContext.Provider value={storeRef.current}>
            {children}
        </ChatStoreContext.Provider>
    )
}
