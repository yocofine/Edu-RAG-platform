<script setup>
defineProps({ name: { type: String, required: true }, size: { type: Number, default: 18 } })
const paths = {
  chat: ['M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z','M8 9h8M8 13h5'],
  files: ['M3 6h6l2 2h10v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
  rules: ['M5 4h14v16H5z','M8 8h8M8 12h8M8 16h5'],
  scripts: ['M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H9l-5 3z','M8 8h8M8 12h6'],
  analysis: ['M4 19V9M10 19V5M16 19v-7M22 19V3','M2 19h22'],
  logs: ['M6 3h12v18H6z','M9 8h6M9 12h6M9 16h4'],
  settings: ['M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z','M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.1 2.1-.1-.1a1.7 1.7 0 0 0-1.9-.3 8 8 0 0 1-1.7.7 1.7 1.7 0 0 0-1.1 1.5V21h-3v-.1a1.7 1.7 0 0 0-1.1-1.5 8 8 0 0 1-1.7-.7 1.7 1.7 0 0 0-1.9.3l-.1.1L3 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 8 8 0 0 1-.7-1.7A1.7 1.7 0 0 0 1.2 12H1V9h.2a1.7 1.7 0 0 0 1.5-1.1 8 8 0 0 1 .7-1.7 1.7 1.7 0 0 0-.3-1.9L3 4.2 5.1 2l.1.1a1.7 1.7 0 0 0 1.9.3 8 8 0 0 1 1.7-.7A1.7 1.7 0 0 0 10 0h3a1.7 1.7 0 0 0 1.1 1.5 8 8 0 0 1 1.7.7 1.7 1.7 0 0 0 1.9-.3l.1-.1L20 4l-.1.1a1.7 1.7 0 0 0-.3 1.9 8 8 0 0 1 .7 1.7A1.7 1.7 0 0 0 21.8 9h.2v3h-.2a1.7 1.7 0 0 0-1.5 1.1 8 8 0 0 1-.9 1.9z'],
  members: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2','M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z','M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8'],
  upload: ['M12 16V4M7 9l5-5 5 5','M4 20h16'],
  history: ['M3 12a9 9 0 1 0 3-6.7L3 8','M3 3v5h5','M12 7v5l3 2'],
  plus: ['M12 5v14M5 12h14'],
  send: ['M22 2 11 13','M22 2 15 22l-4-9-9-4z'],
  close: ['M6 6l12 12M18 6 6 18'],
  menu: ['M4 7h16M4 12h16M4 17h16'],
  more: ['M5 12h.01M12 12h.01M19 12h.01'],
  download: ['M12 3v12M7 10l5 5 5-5','M5 21h14'],
  eye: ['M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z','M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
  book: ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4H6.5A2.5 2.5 0 0 0 4 6.5z','M4 6.5v13'],
  user: ['M20 21a8 8 0 0 0-16 0','M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z'],
  assistant: ['M12 3v3M12 18v3M3 12h3M18 12h3','M8 8h8v8H8z'],
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path v-for="item in (paths[name] || paths.files)" :key="item" :d="item" />
  </svg>
</template>

