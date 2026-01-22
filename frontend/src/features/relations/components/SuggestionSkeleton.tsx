export function SuggestionSkeleton() {
  return (
    <div className="space-y-2" aria-hidden="true" aria-label="Loading recommendations">
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="flex items-center justify-between p-3 rounded-lg animate-pulse"
        >
          {/* Left: Icon + Title */}
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <div className="w-2 h-2 rounded-full bg-gray-200 flex-shrink-0" />
            <div className="h-4 w-2/3 bg-gray-200 rounded" />
          </div>

          {/* Middle: Score badge */}
          <div className="h-5 w-12 bg-gray-200 rounded-full ml-2" />

          {/* Right: Action buttons */}
          <div className="flex items-center gap-1 ml-2">
            <div className="h-8 w-8 rounded-md bg-gray-200" />
            <div className="h-8 w-8 rounded-md bg-gray-200" />
          </div>
        </div>
      ))}
    </div>
  )
}
