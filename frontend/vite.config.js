import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      allow: [
        // Allow serving files from the workspace root
        './',
        // Allow serving the generated DALL-E avatars from the temp storage
        '/Users/virajdalsania/.gemini/antigravity/brain/339fa39d-4f20-42b4-88bb-cbe48ea78027/'
      ]
    }
  }
})
