"""Runnable interview questions curated from LeetCode Hot 100.

The pool intentionally favors questions whose inputs and outputs can be represented
as JSON so candidates can run examples safely in the browser without executing
untrusted code on the API server.
"""

from __future__ import annotations

import random
import secrets
from typing import Any


DIFFICULTY_WEIGHTS = {
    "easy": 0.35,
    "mid": 0.60,
    "hard": 0.05,
}

PROBLEM_CONSTRAINTS = {
    "two-sum": ["2 ≤ nums.length ≤ 10⁴", "-10⁹ ≤ nums[i], target ≤ 10⁹", "只会存在一个有效答案"],
    "valid-parentheses": ["0 ≤ s.length ≤ 10⁴", "s 仅由 ()[]{} 组成"],
    "best-time-to-buy-and-sell-stock": ["1 ≤ prices.length ≤ 10⁵", "0 ≤ prices[i] ≤ 10⁴"],
    "climbing-stairs": ["1 ≤ n ≤ 45"],
    "single-number": ["1 ≤ nums.length ≤ 3×10⁴", "-3×10⁴ ≤ nums[i] ≤ 3×10⁴", "除一个元素外，其余元素均恰好出现两次"],
    "majority-element": ["1 ≤ nums.length ≤ 5×10⁴", "-10⁹ ≤ nums[i] ≤ 10⁹", "多数元素一定存在"],
    "counting-bits": ["0 ≤ n ≤ 10⁵"],
    "find-all-numbers-disappeared-in-an-array": ["1 ≤ nums.length = n ≤ 10⁵", "1 ≤ nums[i] ≤ n"],
    "longest-substring-without-repeating-characters": ["0 ≤ s.length ≤ 5×10⁴", "s 由英文字母、数字、符号和空格组成"],
    "maximum-subarray": ["1 ≤ nums.length ≤ 10⁵", "-10⁴ ≤ nums[i] ≤ 10⁴"],
    "jump-game": ["1 ≤ nums.length ≤ 10⁴", "0 ≤ nums[i] ≤ 10⁵"],
    "unique-paths": ["1 ≤ m, n ≤ 100", "答案保证不超过 2×10⁹"],
    "minimum-path-sum": ["1 ≤ grid.length, grid[i].length ≤ 200", "0 ≤ grid[i][j] ≤ 200"],
    "number-of-islands": ["1 ≤ grid.length, grid[i].length ≤ 300", "grid[i][j] 只能是字符串 '0' 或 '1'"],
    "subarray-sum-equals-k": ["1 ≤ nums.length ≤ 2×10⁴", "-1000 ≤ nums[i] ≤ 1000", "-10⁷ ≤ k ≤ 10⁷"],
    "product-of-array-except-self": ["2 ≤ nums.length ≤ 10⁵", "-30 ≤ nums[i] ≤ 30", "任意前缀或后缀乘积均在 32 位整数范围内", "不能使用除法"],
    "daily-temperatures": ["1 ≤ temperatures.length ≤ 10⁵", "30 ≤ temperatures[i] ≤ 100"],
    "longest-consecutive-sequence": ["0 ≤ nums.length ≤ 10⁵", "-10⁹ ≤ nums[i] ≤ 10⁹", "要求平均 O(n) 时间复杂度"],
    "coin-change": ["1 ≤ coins.length ≤ 12", "1 ≤ coins[i] ≤ 2³¹-1", "0 ≤ amount ≤ 10⁴"],
    "word-break": ["1 ≤ s.length ≤ 300", "1 ≤ wordDict.length ≤ 1000", "字典单词可重复使用，且列表中无重复词"],
    "decode-string": ["1 ≤ s.length ≤ 30", "输入保证有效且不含多余空格", "所有整数范围为 [1, 300]，输出长度不超过 10⁵"],
    "trapping-rain-water": ["1 ≤ height.length ≤ 2×10⁴", "0 ≤ height[i] ≤ 10⁵"],
    "minimum-window-substring": ["1 ≤ s.length, t.length ≤ 10⁵", "s 和 t 由英文字母组成", "若存在答案，保证最短覆盖子串唯一"],
    "sliding-window-maximum": ["1 ≤ nums.length ≤ 10⁵", "-10⁴ ≤ nums[i] ≤ 10⁴", "1 ≤ k ≤ nums.length"],
}


def _question(
    title: str,
    slug: str,
    difficulty: str,
    description: str,
    function_name: str,
    parameters: str,
    tests: list[dict[str, Any]],
    rubric: list[str],
) -> dict[str, Any]:
    parameter_names = [item.strip() for item in parameters.split(",")]
    javascript_starter = (
        f"function {function_name}({parameters}) {{\n"
        "  // 在这里编写你的解法\n"
        "  \n"
        "}\n"
    )
    typescript_parameters = ", ".join(
        f"{parameter}: any" for parameter in parameter_names
    )
    typescript_starter = (
        f"function {function_name}({typescript_parameters}): any {{\n"
        "  // 在这里编写你的解法\n"
        "  \n"
        "}\n"
    )
    python_starter = (
        f"def {function_name}({', '.join(parameter_names)}):\n"
        "    # 在这里编写你的解法\n"
        "    pass\n"
    )
    return {
        "title": title,
        "slug": slug,
        "difficulty": difficulty,
        "description": description,
        "parameters": parameter_names,
        "constraints": PROBLEM_CONSTRAINTS.get(slug, []),
        "functionName": function_name,
        "starterCode": javascript_starter,
        "starterCodes": {
            "javascript": javascript_starter,
            "typescript": typescript_starter,
            "python": python_starter,
        },
        "testCases": tests,
        "rubric": rubric,
    }


HOT_100_RUNNABLE_QUESTIONS = [
    _question(
        "两数之和",
        "two-sum",
        "easy",
        "给定整数数组 nums 和目标值 target，请返回和为 target 的两个元素下标。每组输入只对应一个答案，不能重复使用同一元素。",
        "twoSum",
        "nums, target",
        [
            {"args": [[2, 7, 11, 15], 9], "expected": [0, 1]},
            {"args": [[3, 2, 4], 6], "expected": [1, 2]},
        ],
        ["使用哈希表记录已访问值与下标", "时间复杂度 O(n)", "正确处理重复元素与下标"],
    ),
    _question(
        "有效的括号",
        "valid-parentheses",
        "easy",
        "给定只包含 ()[]{} 的字符串 s，判断括号是否有效。括号必须以正确顺序闭合。",
        "isValid",
        "s",
        [
            {"args": ["()[]{}"], "expected": True},
            {"args": ["([)]"], "expected": False},
            {"args": ["{[]}"], "expected": True},
        ],
        ["使用栈匹配左右括号", "时间复杂度 O(n)", "处理空栈和未闭合括号"],
    ),
    _question(
        "买卖股票的最佳时机",
        "best-time-to-buy-and-sell-stock",
        "easy",
        "给定 prices，其中 prices[i] 是第 i 天股价。最多完成一次买入和一次卖出，返回最大利润；不能获利时返回 0。",
        "maxProfit",
        "prices",
        [
            {"args": [[7, 1, 5, 3, 6, 4]], "expected": 5},
            {"args": [[7, 6, 4, 3, 1]], "expected": 0},
        ],
        ["一次遍历维护历史最低价", "时间复杂度 O(n)", "不能先卖后买"],
    ),
    _question(
        "爬楼梯",
        "climbing-stairs",
        "easy",
        "每次可以爬 1 或 2 个台阶。给定 n，返回到达第 n 阶的不同方法数。",
        "climbStairs",
        "n",
        [
            {"args": [2], "expected": 2},
            {"args": [5], "expected": 8},
        ],
        ["识别斐波那契型状态转移", "时间复杂度 O(n)", "可将空间优化到 O(1)"],
    ),
    _question(
        "只出现一次的数字",
        "single-number",
        "easy",
        "非空整数数组中除一个元素只出现一次外，其余元素均出现两次。返回只出现一次的元素。",
        "singleNumber",
        "nums",
        [
            {"args": [[2, 2, 1]], "expected": 1},
            {"args": [[4, 1, 2, 1, 2]], "expected": 4},
        ],
        ["利用异或的交换律与自反性", "时间复杂度 O(n)", "额外空间 O(1)"],
    ),
    _question(
        "多数元素",
        "majority-element",
        "easy",
        "给定长度为 n 的数组 nums，返回出现次数超过 n/2 的多数元素。题目保证多数元素存在。",
        "majorityElement",
        "nums",
        [
            {"args": [[3, 2, 3]], "expected": 3},
            {"args": [[2, 2, 1, 1, 1, 2, 2]], "expected": 2},
        ],
        ["Boyer-Moore 投票或等价正确方案", "时间复杂度 O(n)", "说明多数元素存在这一前提"],
    ),
    _question(
        "比特位计数",
        "counting-bits",
        "easy",
        "给定整数 n，返回长度为 n + 1 的数组 ans，其中 ans[i] 是 i 的二进制表示中 1 的个数。",
        "countBits",
        "n",
        [
            {"args": [2], "expected": [0, 1, 1]},
            {"args": [5], "expected": [0, 1, 1, 2, 1, 2]},
        ],
        ["使用动态规划或最低有效位递推", "整体时间复杂度 O(n)", "结果包含 0 到 n"],
    ),
    _question(
        "找到所有数组中消失的数字",
        "find-all-numbers-disappeared-in-an-array",
        "easy",
        "给定长度为 n 的数组 nums，元素范围为 [1,n]。返回 [1,n] 中未出现在 nums 中的所有整数。",
        "findDisappearedNumbers",
        "nums",
        [
            {"args": [[4, 3, 2, 7, 8, 2, 3, 1]], "expected": [5, 6]},
            {"args": [[1, 1]], "expected": [2]},
        ],
        ["利用下标原地标记或等价线性方案", "时间复杂度 O(n)", "正确处理重复值"],
    ),
    _question(
        "无重复字符的最长子串",
        "longest-substring-without-repeating-characters",
        "mid",
        "给定字符串 s，返回不含重复字符的最长子串长度。",
        "lengthOfLongestSubstring",
        "s",
        [
            {"args": ["abcabcbb"], "expected": 3},
            {"args": ["bbbbb"], "expected": 1},
            {"args": [""], "expected": 0},
        ],
        ["滑动窗口维护字符最近位置", "时间复杂度 O(n)", "窗口左边界不能回退"],
    ),
    _question(
        "最大子数组和",
        "maximum-subarray",
        "mid",
        "给定整数数组 nums，返回具有最大和的连续子数组的和。",
        "maxSubArray",
        "nums",
        [
            {"args": [[-2, 1, -3, 4, -1, 2, 1, -5, 4]], "expected": 6},
            {"args": [[-3, -2, -5]], "expected": -2},
        ],
        ["Kadane 动态规划或等价方案", "时间复杂度 O(n)", "正确处理全负数组"],
    ),
    _question(
        "跳跃游戏",
        "jump-game",
        "mid",
        "数组 nums[i] 表示从位置 i 最多可向前跳的步数。判断能否到达最后一个下标。",
        "canJump",
        "nums",
        [
            {"args": [[2, 3, 1, 1, 4]], "expected": True},
            {"args": [[3, 2, 1, 0, 4]], "expected": False},
        ],
        ["贪心维护最远可达位置", "时间复杂度 O(n)", "及时识别不可达下标"],
    ),
    _question(
        "不同路径",
        "unique-paths",
        "mid",
        "机器人位于 m×n 网格左上角，每次只能向右或向下移动，返回到达右下角的不同路径数。",
        "uniquePaths",
        "m, n",
        [
            {"args": [3, 7], "expected": 28},
            {"args": [3, 2], "expected": 3},
        ],
        ["正确建立二维或一维动态规划", "时间复杂度 O(mn)", "初始化首行与首列"],
    ),
    _question(
        "最小路径和",
        "minimum-path-sum",
        "mid",
        "给定非负整数网格 grid，从左上角到右下角每次只能向右或向下，返回路径上的最小数字总和。",
        "minPathSum",
        "grid",
        [
            {"args": [[[1, 3, 1], [1, 5, 1], [4, 2, 1]]], "expected": 7},
            {"args": [[[1, 2, 3], [4, 5, 6]]], "expected": 12},
        ],
        ["动态规划取上方与左方最优值", "时间复杂度 O(mn)", "正确处理边界行列"],
    ),
    _question(
        "岛屿数量",
        "number-of-islands",
        "mid",
        "给定由字符 '1'（陆地）和 '0'（水）组成的二维网格，返回四方向相连的岛屿数量。",
        "numIslands",
        "grid",
        [
            {"args": [[["1", "1", "0"], ["1", "0", "0"], ["0", "0", "1"]]], "expected": 2},
            {"args": [[["0", "0"], ["0", "0"]]], "expected": 0},
        ],
        ["DFS/BFS 或并查集遍历连通块", "时间复杂度 O(mn)", "访问标记避免重复计算"],
    ),
    _question(
        "和为 K 的子数组",
        "subarray-sum-equals-k",
        "mid",
        "给定整数数组 nums 和整数 k，返回和等于 k 的连续子数组个数。",
        "subarraySum",
        "nums, k",
        [
            {"args": [[1, 1, 1], 2], "expected": 2},
            {"args": [[1, 2, 3], 3], "expected": 2},
        ],
        ["前缀和结合频次哈希表", "时间复杂度 O(n)", "初始前缀和 0 的频次为 1"],
    ),
    _question(
        "除自身以外数组的乘积",
        "product-of-array-except-self",
        "mid",
        "给定整数数组 nums，不使用除法，在 O(n) 时间内返回 answer，其中 answer[i] 等于 nums 中除 nums[i] 外其余元素的乘积。",
        "productExceptSelf",
        "nums",
        [
            {"args": [[1, 2, 3, 4]], "expected": [24, 12, 8, 6]},
            {"args": [[-1, 1, 0, -3, 3]], "expected": [0, 0, 9, 0, 0]},
        ],
        ["组合前缀积与后缀积", "时间复杂度 O(n)", "不使用除法并处理 0"],
    ),
    _question(
        "每日温度",
        "daily-temperatures",
        "mid",
        "给定每日温度 temperatures，返回数组 answer，使 answer[i] 是第 i 天后首次出现更高温度还需等待的天数；不存在则为 0。",
        "dailyTemperatures",
        "temperatures",
        [
            {"args": [[73, 74, 75, 71, 69, 72, 76, 73]], "expected": [1, 1, 4, 2, 1, 1, 0, 0]},
            {"args": [[30, 40, 50, 60]], "expected": [1, 1, 1, 0]},
        ],
        ["维护单调递减下标栈", "时间复杂度 O(n)", "栈中保存下标而非温度值"],
    ),
    _question(
        "最长连续序列",
        "longest-consecutive-sequence",
        "mid",
        "给定未排序整数数组 nums，返回数字连续的最长序列长度。要求时间复杂度 O(n)。",
        "longestConsecutive",
        "nums",
        [
            {"args": [[100, 4, 200, 1, 3, 2]], "expected": 4},
            {"args": [[0, 3, 7, 2, 5, 8, 4, 6, 0, 1]], "expected": 9},
        ],
        ["哈希集合并只从序列起点扩展", "平均时间复杂度 O(n)", "正确处理重复元素"],
    ),
    _question(
        "零钱兑换",
        "coin-change",
        "mid",
        "给定硬币面额 coins 和总金额 amount，返回凑成金额所需的最少硬币数；无法凑成时返回 -1。",
        "coinChange",
        "coins, amount",
        [
            {"args": [[1, 2, 5], 11], "expected": 3},
            {"args": [[2], 3], "expected": -1},
            {"args": [[1], 0], "expected": 0},
        ],
        ["完全背包动态规划或 BFS", "时间复杂度 O(amount×coins)", "正确初始化不可达状态"],
    ),
    _question(
        "单词拆分",
        "word-break",
        "mid",
        "给定字符串 s 和单词列表 wordDict，判断 s 是否能由字典中的一个或多个单词拼接而成，字典单词可以重复使用。",
        "wordBreak",
        "s, wordDict",
        [
            {"args": ["leetcode", ["leet", "code"]], "expected": True},
            {"args": ["catsandog", ["cats", "dog", "sand", "and", "cat"]], "expected": False},
        ],
        ["动态规划定义前缀可拆分状态", "控制枚举边界", "正确处理可重复使用的字典词"],
    ),
    _question(
        "字符串解码",
        "decode-string",
        "mid",
        "编码规则为 k[encoded_string]，表示方括号内字符串重复 k 次；输入保证有效，返回解码后的字符串。",
        "decodeString",
        "s",
        [
            {"args": ["3[a]2[bc]"], "expected": "aaabcbc"},
            {"args": ["3[a2[c]]"], "expected": "accaccacc"},
        ],
        ["使用栈或递归处理嵌套结构", "正确解析多位重复次数", "时间复杂度与输出规模相匹配"],
    ),
    _question(
        "接雨水",
        "trapping-rain-water",
        "hard",
        "给定非负整数数组 height 表示柱状图高度，计算下雨后能够接住的雨水总量。",
        "trap",
        "height",
        [
            {"args": [[0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]], "expected": 6},
            {"args": [[4, 2, 0, 3, 2, 5]], "expected": 9},
        ],
        ["双指针、单调栈或前后缀最大值", "时间复杂度 O(n)", "理解水位由较低侧边界决定"],
    ),
    _question(
        "最小覆盖子串",
        "minimum-window-substring",
        "hard",
        "给定字符串 s 和 t，返回 s 中涵盖 t 所有字符（含重复次数）的最短子串；不存在时返回空字符串。",
        "minWindow",
        "s, t",
        [
            {"args": ["ADOBECODEBANC", "ABC"], "expected": "BANC"},
            {"args": ["a", "aa"], "expected": ""},
        ],
        ["滑动窗口维护需求字符计数", "正确收缩到最小合法窗口", "时间复杂度 O(|s|+|t|)"],
    ),
    _question(
        "滑动窗口最大值",
        "sliding-window-maximum",
        "hard",
        "给定整数数组 nums 和窗口大小 k，窗口每次向右移动一位，返回每个窗口中的最大值。",
        "maxSlidingWindow",
        "nums, k",
        [
            {"args": [[1, 3, -1, -3, 5, 3, 6, 7], 3], "expected": [3, 3, 5, 5, 6, 7]},
            {"args": [[1], 1], "expected": [1]},
        ],
        ["使用维护下标的单调递减队列", "每个元素至多入队出队一次", "及时移除窗口外下标"],
    ),
]


def build_algorithm_questions(
    count: int,
    *,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Select one or two weighted, non-repeating Hot 100 questions."""
    count = max(0, min(2, int(count)))
    if count == 0:
        return []
    chooser = rng or secrets.SystemRandom()
    available = list(HOT_100_RUNNABLE_QUESTIONS)
    selected: list[dict[str, Any]] = []
    difficulties = tuple(DIFFICULTY_WEIGHTS)
    for algorithm_index in range(1, count + 1):
        populated = {
            difficulty
            for difficulty in difficulties
            if any(item["difficulty"] == difficulty for item in available)
        }
        eligible_difficulties = [
            difficulty for difficulty in difficulties if difficulty in populated
        ]
        eligible_weights = [
            DIFFICULTY_WEIGHTS[difficulty] for difficulty in eligible_difficulties
        ]
        difficulty = chooser.choices(
            eligible_difficulties,
            weights=eligible_weights,
            k=1,
        )[0]
        candidates = [
            item for item in available if item["difficulty"] == difficulty
        ]
        picked = chooser.choice(candidates)
        available.remove(picked)
        metadata = {
            key: picked[key]
            for key in (
                "title",
                "slug",
                "difficulty",
                "description",
                "parameters",
                "constraints",
                "functionName",
                "starterCode",
                "starterCodes",
                "testCases",
            )
        }
        selected.append(
            {
                "category": "algorithm",
                "question": (
                    f"算法题{algorithm_index}-{picked['title']}-{picked['difficulty']}"
                    f"\n\n{picked['description']}"
                ),
                "followUp": "请说明你的时间复杂度、空间复杂度，以及最容易遗漏的边界条件。",
                "rubric": list(picked["rubric"]),
                "source": None,
                "metadata": metadata,
                "agentGenerated": False,
            }
        )
    return selected
