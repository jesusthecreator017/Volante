// Widget extension that draws the pod session Live Activity (Lock Screen and Dynamic Island). Layouts live in
// SharedLive/LiveSessionActivity.swift so the app's DEBUG preview screen draws the same views.

import ActivityKit
import SwiftUI
import WidgetKit

@main
struct OsoLiveBundle: WidgetBundle {
    var body: some Widget {
        LiveSessionWidget()
        WeeklyGoalWidget()
    }
}

struct LiveSessionWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: LiveSessionAttributes.self) { context in
            LiveLockScreenView(attributes: context.attributes, state: context.state)
                .activityBackgroundTint(nil)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { LiveExpandedLeading(attributes: context.attributes, state: context.state) }
                DynamicIslandExpandedRegion(.trailing) { LiveExpandedTrailing(state: context.state) }
                DynamicIslandExpandedRegion(.bottom) { LiveExpandedBottom(attributes: context.attributes, state: context.state) }
            } compactLeading: {
                LiveCompactLeading()
            } compactTrailing: {
                LiveCompactTrailing(attributes: context.attributes, state: context.state)
            } minimal: {
                LiveMinimal(attributes: context.attributes, state: context.state)
            }
            .keylineTint(LiveStyle.shuttle)
        }
        .supplementalActivityFamilies([.small])  // the Smart Stack on a paired Apple Watch
    }
}
