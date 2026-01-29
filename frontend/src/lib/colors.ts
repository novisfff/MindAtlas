// Material Design 600 Series - optimized for text contrast on white/light backgrounds
export const MATERIAL_COLORS = [
  '#E53935', // Red 600
  '#D81B60', // Pink 600
  '#8E24AA', // Purple 600
  '#5E35B1', // Deep Purple 600
  '#3949AB', // Indigo 600
  '#1E88E5', // Blue 600
  '#039BE5', // Light Blue 600
  '#00ACC1', // Cyan 600
  '#00897B', // Teal 600
  '#43A047', // Green 600
  '#7CB342', // Light Green 600
  '#C0CA33', // Lime 600
  '#FDD835', // Yellow 600
  '#FFB300', // Amber 600
  '#FB8C00', // Orange 600
  '#F4511E', // Deep Orange 600
  '#6D4C41', // Brown 600
  '#546E7A', // Blue Grey 600
]

/**
 * Deterministically pick a color based on name (hash mapping).
 * Same name always returns the same color.
 */
export function getColorByName(name: string): string {
  if (!name) return MATERIAL_COLORS[0]

  let hash = 0
  const normalized = name.trim().toLowerCase()
  for (let i = 0; i < normalized.length; i++) {
    hash = normalized.charCodeAt(i) + ((hash << 5) - hash)
  }

  const index = Math.abs(hash) % MATERIAL_COLORS.length
  return MATERIAL_COLORS[index]
}

/**
 * Get a random color from the palette.
 * Useful for new item creation where name is not yet known.
 */
export function getRandomColor(): string {
  const index = Math.floor(Math.random() * MATERIAL_COLORS.length)
  return MATERIAL_COLORS[index]
}
