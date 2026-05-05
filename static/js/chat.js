let currentImageData = null;
let currentMediaType = null;
let currentChatController = null;

// Auto-resize textarea
const textarea = document.getElementById('message-input');
textarea.addEventListener('input', function() {
    this.style.height = '28px';
    this.style.height = (this.scrollHeight) + 'px';
});

function appendMessage(content, isUser = false) {
    const messagesDiv = document.getElementById('chat-messages');
    const messageWrapper = document.createElement('div');
    messageWrapper.className = isUser ? 'message-wrapper user-message' : 'message-wrapper';
    
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message-row';
    
    // Avatar
    const avatarDiv = document.createElement('div');
    if (isUser) {
        avatarDiv.className = 'avatar user-avatar';
        avatarDiv.textContent = 'You';
    } else {
        avatarDiv.className = 'avatar ai-avatar';
        avatarDiv.setAttribute('aria-label', 'Orange cat assistant');
        avatarDiv.textContent = '🐱';
    }
    
    // Message content
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const innerDiv = document.createElement('div');
    innerDiv.className = isUser
        ? 'message-card user-card prose prose-slate max-w-none'
        : 'message-card assistant-card prose prose-slate max-w-none';
    
    if (!isUser && content) {
        try {
            innerDiv.innerHTML = marked.parse(content);
        } catch (e) {
            console.error('Error parsing markdown:', e);
            innerDiv.textContent = content;
        }
    } else {
        innerDiv.textContent = content || '';
    }
    
    contentDiv.appendChild(innerDiv);
    messageDiv.appendChild(avatarDiv);
    messageDiv.appendChild(contentDiv);
    messageWrapper.appendChild(messageDiv);
    messagesDiv.appendChild(messageWrapper);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Event Listeners
document.getElementById('upload-btn').addEventListener('click', () => {
    document.getElementById('file-input').click();
});

document.getElementById('file-input').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (file) {
        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            
            if (data.success) {
                currentImageData = data.image_data;
                currentMediaType = data.media_type;
                document.getElementById('preview-img').src = `data:${data.media_type};base64,${data.image_data}`;
                document.getElementById('image-preview').classList.remove('hidden');
            }
        } catch (error) {
            console.error('Error uploading image:', error);
        }
    }
});

document.getElementById('remove-image').addEventListener('click', () => {
    currentImageData = null;
    document.getElementById('image-preview').classList.add('hidden');
    document.getElementById('file-input').value = '';
});

function appendThinkingIndicator() {
    const messagesDiv = document.getElementById('chat-messages');
    const messageWrapper = document.createElement('div');
    messageWrapper.className = 'message-wrapper thinking-message';
    
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message-row';
    
    // AI Avatar
    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'avatar ai-avatar';
    avatarDiv.setAttribute('aria-label', 'Orange cat assistant');
    avatarDiv.textContent = '🐱';
    
    // Thinking content
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const thinkingDiv = document.createElement('div');
    thinkingDiv.className = 'thinking';
    thinkingDiv.innerHTML = '<div style="margin-top: 6px; margin-bottom: 4px;">Thinking<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span></div>';
    
    contentDiv.appendChild(thinkingDiv);
    messageDiv.appendChild(avatarDiv);
    messageDiv.appendChild(contentDiv);
    messageWrapper.appendChild(messageDiv);
    messagesDiv.appendChild(messageWrapper);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    
    return messageWrapper;
}

// Enter sends; Shift+Enter inserts a newline.
document.getElementById('message-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        document.getElementById('chat-form').dispatchEvent(new Event('submit'));
    }
});

// Add function to show tool usage
function appendToolUsage(toolName) {
    const messagesDiv = document.getElementById('chat-messages');
    const messageWrapper = document.createElement('div');
    messageWrapper.className = 'message-wrapper';
    
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message-row';
    
    // AI Avatar
    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'avatar ai-avatar';
    avatarDiv.setAttribute('aria-label', 'Orange cat assistant');
    avatarDiv.textContent = '🐱';
    
    // Tool usage content
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const toolDiv = document.createElement('div');
    toolDiv.className = 'tool-usage';
    toolDiv.textContent = `Using tool: ${toolName}`;
    
    contentDiv.appendChild(toolDiv);
    messageDiv.appendChild(avatarDiv);
    messageDiv.appendChild(contentDiv);
    messageWrapper.appendChild(messageDiv);
    messagesDiv.appendChild(messageWrapper);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// Add this function near the top of your file
function updateTokenUsage(usedTokens, maxTokens) {
    const safeMaxTokens = Number(maxTokens) || 1;
    const safeUsedTokens = Number(usedTokens) || 0;
    const percentage = Math.min(100, (safeUsedTokens / safeMaxTokens) * 100);
    const tokenBar = document.getElementById('token-bar');
    const tokensUsed = document.getElementById('tokens-used');
    const maxTokensLabel = document.getElementById('max-tokens');
    const tokenPercentage = document.getElementById('token-percentage');
    
    // Update the numbers
    tokensUsed.textContent = safeUsedTokens.toLocaleString();
    maxTokensLabel.textContent = safeMaxTokens.toLocaleString();
    tokenPercentage.textContent = `${percentage.toFixed(1)}%`;
    
    // Update the bar
    tokenBar.style.width = `${percentage}%`;
    
    // Update colors based on usage
    tokenBar.classList.remove('warning', 'danger');
    if (percentage > 90) {
        tokenBar.classList.add('danger');
    } else if (percentage > 75) {
        tokenBar.classList.add('warning');
    }
}

function clearMessageList() {
    const messagesDiv = document.getElementById('chat-messages');
    const messages = messagesDiv.getElementsByClassName('message-wrapper');
    while (messages.length > 1) {
        messages[1].remove();
    }
    messagesDiv.scrollTop = 0;
}

function clearLocalInputState() {
    currentImageData = null;
    currentMediaType = null;
    document.getElementById('image-preview')?.classList.add('hidden');
    document.getElementById('file-input').value = '';
    document.getElementById('message-input').value = '';
    resetTextarea();
}

async function clearConversation() {
    if (currentChatController) {
        currentChatController.abort();
        currentChatController = null;
    }

    clearMessageList();
    clearLocalInputState();

    try {
        const response = await fetch('/reset', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        const data = await response.json().catch(() => null);
        const maxTokens = data?.token_usage?.max_tokens || parseInt(
            document.getElementById('max-tokens').textContent.replace(/,/g, ''),
            10
        );
        updateTokenUsage(0, maxTokens);
    } catch (error) {
        console.error('Error clearing conversation:', error);
    }
}

// Update the chat form submit handler
document.getElementById('chat-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const messageInput = document.getElementById('message-input');
    const message = messageInput.value.trim();
    
    if (!message && !currentImageData) return;

    if (!currentImageData && message === '/clear') {
        await clearConversation();
        return;
    }
    
    // Append user message (and image if present)
    appendMessage(message, true);
    if (currentImageData) {
        // Optionally show the image in the chat
        const imagePreview = document.createElement('img');
        imagePreview.src = `data:${currentMediaType || 'image/jpeg'};base64,${currentImageData}`;
        imagePreview.className = 'image-preview-thumb mt-2';
        document.querySelector('.message-wrapper:last-child .prose').appendChild(imagePreview);
    }
    
    // Clear input and reset height
    messageInput.value = '';
    resetTextarea();
    
    try {
        // Add thinking indicator
        const thinkingMessage = appendThinkingIndicator();
        currentChatController = new AbortController();
        
        const response = await fetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: message,
                image: currentImageData  // This will be null if no image is selected
            }),
            signal: currentChatController.signal
        });
        currentChatController = null;
        
        const data = await response.json();
        
        // Update token usage if provided in response
        if (data.token_usage) {
            updateTokenUsage(data.token_usage.total_tokens, data.token_usage.max_tokens);
        }
        
        // Remove thinking indicator
        if (thinkingMessage) {
            thinkingMessage.remove();
        }
        
        // Show tool usage if present
        if (data.tool_name) {
            appendToolUsage(data.tool_name);
        }
        
        // Show response if we have one
        if (data && data.response) {
            appendMessage(data.response);
        } else {
            appendMessage('Error: No response received');
        }
        
        // Clear image after sending
        currentImageData = null;
        currentMediaType = null;
        document.getElementById('image-preview').classList.add('hidden');
        document.getElementById('file-input').value = '';
        
    } catch (error) {
        currentChatController = null;
        if (error.name === 'AbortError') {
            return;
        }
        console.error('Error sending message:', error);
        document.querySelector('.thinking-message')?.remove();
        appendMessage('Error: Failed to send message');
    }
});

function resetTextarea() {
    const textarea = document.getElementById('message-input');
    textarea.style.height = '28px';
}

document.getElementById('chat-form').addEventListener('reset', () => {
    resetTextarea();
});

// Add at the top of the file
window.addEventListener('load', async () => {
    try {
        await clearConversation();
    } catch (error) {
        console.error('Error resetting conversation:', error);
    }
}); 
