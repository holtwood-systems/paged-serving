//! 调度器类型

use super::memory::PhysicalBlockRef;
use super::request::Request;
use super::{BlockIdx, RequestId, SeqId, TokenId};

/// 序列
///
/// 活跃请求及其 KV Cache 块的集合。
#[derive(Debug, Clone)]
pub struct Sequence {
    /// 序列唯一标识符
    pub seq_id: SeqId,

    /// 关联的请求
    pub request: Request,

    /// KV Cache 块（按逻辑顺序排列的物理块引用）
    pub logical_blocks: Vec<PhysicalBlockRef>,

    /// 已生成的 token 数
    pub num_generated_tokens: u32,
}

impl Sequence {
    /// 从请求创建序列
    pub fn new(seq_id: SeqId, request: Request) -> Self {
        Self {
            seq_id,
            request,
            logical_blocks: Vec::new(),
            num_generated_tokens: 0,
        }
    }

    /// 获取块表（物理块索引列表）用于 GPU 执行
    pub fn get_block_table(&self) -> Vec<BlockIdx> {
        self.logical_blocks.iter().map(|b| b.block_idx).collect()
    }

    /// 生成 prefill 调度快照（只复制 prompt 与块表）。
    pub fn to_scheduled_prefill(&self) -> ScheduledPrefill {
        ScheduledPrefill {
            seq_id: self.seq_id,
            request_id: self.request.id,
            input_tokens: self.request.input_tokens.clone(),
            context_len: self.context_len(),
            block_table: self.get_block_table(),
        }
    }

    /// 生成 decode 调度快照（只带最后一个 token、位置与块表）。
    pub fn to_scheduled_decode(&self) -> ScheduledDecode {
        ScheduledDecode {
            seq_id: self.seq_id,
            request_id: self.request.id,
            input_token: self.decode_input_token(),
            position: self.decode_position(),
            context_len: self.context_len(),
            block_table: self.get_block_table(),
        }
    }

    /// 计算上下文长度（输入 + 已生成）
    ///
    /// 使用饱和算术，任何输入下都不会溢出。
    pub fn context_len(&self) -> u32 {
        let input_len = u32::try_from(self.request.input_tokens.len()).unwrap_or(u32::MAX);
        input_len.saturating_add(self.num_generated_tokens)
    }

    /// 获取 decode 阶段的输入 token
    pub fn decode_input_token(&self) -> Option<TokenId> {
        self.request
            .output_tokens
            .last()
            .copied()
            .or_else(|| self.request.input_tokens.last().copied())
    }

    /// 获取 decode 阶段的位置
    pub fn decode_position(&self) -> Option<u32> {
        self.context_len().checked_sub(1)
    }
}

/// 调度器输出中的 prefill 序列。
///
/// 只携带 `build_execution_batch` 真正需要的字段，而不是克隆整个
/// [`Sequence`]/[`Request`]（后者会复制不断增长的输出 token 与 logprobs）。
#[derive(Debug, Clone)]
pub struct ScheduledPrefill {
    pub seq_id: SeqId,
    pub request_id: RequestId,
    /// 完整 prompt；prefill 只调度一次，这次复制是必要的。
    pub input_tokens: Vec<TokenId>,
    pub context_len: u32,
    pub block_table: Vec<BlockIdx>,
}

/// 调度器输出中的 decode 序列。
///
/// 每步只带最后一个 token、位置与块表；`input_token` / `position` 为
/// `None` 表示调度器状态不一致，由执行流水线报错。
#[derive(Debug, Clone)]
pub struct ScheduledDecode {
    pub seq_id: SeqId,
    pub request_id: RequestId,
    pub input_token: Option<TokenId>,
    pub position: Option<u32>,
    pub context_len: u32,
    pub block_table: Vec<BlockIdx>,
}

/// 调度器输出
///
/// 包含一个调度周期内待执行的序列快照。快照只含执行批次需要的轻量字段，
/// 输出仅在单线程内被执行流水线读取一次后即丢弃。
#[derive(Debug, Clone, Default)]
pub struct SchedulerOutput {
    /// Prefill 阶段的序列
    pub prefill_sequences: Vec<ScheduledPrefill>,

    /// Decode 阶段的序列
    pub decode_sequences: Vec<ScheduledDecode>,

    /// 批次总 token 数
    pub total_tokens: u32,
}

impl SchedulerOutput {
    /// 检查输出是否为空
    pub fn is_empty(&self) -> bool {
        self.prefill_sequences.is_empty() && self.decode_sequences.is_empty()
    }

    /// 计算序列总数
    pub fn num_sequences(&self) -> usize {
        self.prefill_sequences.len() + self.decode_sequences.len()
    }

    /// 获取所有序列 ID（prefill 和 decode 合并）
    pub fn seq_ids(&self) -> Vec<SeqId> {
        self.prefill_sequences
            .iter()
            .map(|s| s.seq_id)
            .chain(self.decode_sequences.iter().map(|s| s.seq_id))
            .collect()
    }
}
