import { describe, expect, it } from 'vitest'
import { friendlyRuntimeCopy } from './ConversationApp'

describe('conversation public copy', () => {
  it('hides implementation terminology from current and persisted run events', () => {
    expect(friendlyRuntimeCopy('已根据最新 ReAct 决策重排公开计划')).toBe('已根据最新进展调整安排')
    expect(friendlyRuntimeCopy('请求已进入 Agent 队列')).toBe('请求已进入处理队列')
    expect(friendlyRuntimeCopy('结合本轮消息与会话上下文理解需求')).toBe('结合本轮对话理解你的需求')
  })
})
