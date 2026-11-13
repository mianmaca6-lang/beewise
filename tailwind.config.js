/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html", // Aquí Tailwind buscará las clases utilizadas
    "./static/**/*.js"
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}