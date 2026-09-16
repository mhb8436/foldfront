import { useState } from 'react'
import { Plus } from 'lucide-react'

import { api } from '../api/client'
import { useAsync } from '../hooks/useAsync'
import { PageHeader } from '../components/Shell'
import { Empty, ErrorBox, Panel } from '../components/Common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field, Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

const KINDS = [
  'backbone',
  'sequence',
  'solubility',
  'structure',
  'docking',
  'msa',
  'embedding',
  'other',
]

/** The model registry: register by id, manage versions, activation, approval. */
export function Models() {
  const models = useAsync(() => api.listModels(), [])
  const [error, setError] = useState<string | null>(null)
  const [form, setForm] = useState({
    model_id: '',
    version: '',
    kind: 'other',
    endpoint_id: '',
    gpu_count: 1,
  })

  async function register(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await api.registerModel({
        model_id: form.model_id,
        version: form.version,
        kind: form.kind,
        endpoint_id: form.endpoint_id || null,
        active: true,
        is_default: true,
        resources: { gpu_count: Number(form.gpu_count), gpu_memory_gb: null, timeout_seconds: 21600 },
      })
      setForm({ model_id: '', version: '', kind: 'other', endpoint_id: '', gpu_count: 1 })
      models.reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function toggle(modelId: string, version: string, active: boolean) {
    await api.setModelActive(modelId, version, !active)
    models.reload()
  }

  async function approve(modelId: string, version: string) {
    await api.approveModel(modelId, version, 'admin')
    models.reload()
  }

  return (
    <>
      <PageHeader
        title="모델 관리"
        description="모델을 고유 ID 로 등록합니다. URL 을 직접 수정하지 않고 버전·활성 상태·승인을 화면에서 관리합니다."
      />
      <ErrorBox message={error ?? models.error} />

      <Panel title="모델 등록">
        <form onSubmit={register} className="flex flex-wrap items-end gap-3">
          <Field className="w-[160px]">
            <Label htmlFor="model_id">모델 ID</Label>
            <Input
              id="model_id"
              required
              value={form.model_id}
              onChange={(e) => setForm({ ...form, model_id: e.target.value })}
              placeholder="rfd3"
              className="font-mono text-[13px]"
            />
          </Field>
          <Field className="w-[160px]">
            <Label htmlFor="version">버전</Label>
            <Input
              id="version"
              required
              value={form.version}
              onChange={(e) => setForm({ ...form, version: e.target.value })}
              placeholder="2026-09-01"
              className="font-mono text-[13px]"
            />
          </Field>
          <Field className="w-[150px]">
            <Label htmlFor="kind">종류</Label>
            <Select
              id="kind"
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </Select>
          </Field>
          <Field className="w-[180px]">
            <Label htmlFor="endpoint">엔드포인트</Label>
            <Input
              id="endpoint"
              value={form.endpoint_id}
              onChange={(e) => setForm({ ...form, endpoint_id: e.target.value })}
              placeholder="ep-xxxx"
              className="font-mono text-[13px]"
            />
          </Field>
          <Field className="w-[90px]">
            <Label htmlFor="gpu">GPU</Label>
            <Input
              id="gpu"
              type="number"
              min={0}
              max={8}
              value={form.gpu_count}
              onChange={(e) => setForm({ ...form, gpu_count: Number(e.target.value) })}
            />
          </Field>
          <Button type="submit">
            <Plus />
            등록
          </Button>
        </form>
      </Panel>

      <Panel title="등록된 모델" description={`${models.data?.count ?? 0}종`} bodyClassName="p-0">
        {models.data?.items.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>모델</TableHead>
                <TableHead className="w-[140px]">버전</TableHead>
                <TableHead className="w-[120px]">종류</TableHead>
                <TableHead>실행 위치</TableHead>
                <TableHead className="w-[70px]">GPU</TableHead>
                <TableHead className="w-[90px]">상태</TableHead>
                <TableHead className="w-[90px]">승인</TableHead>
                <TableHead className="w-[150px]" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.data.items.map((m) => (
                <TableRow key={`${m.model_id}:${m.version}`}>
                  <TableCell className="font-medium">
                    {m.model_id}
                    {m.is_default && (
                      <Badge variant="secondary" className="badge ml-2">
                        기본
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-[12.5px]">{m.version}</TableCell>
                  <TableCell className="text-muted-foreground">{m.kind}</TableCell>
                  <TableCell className="text-muted-foreground font-mono text-[12.5px]">
                    {m.endpoint_id ?? m.base_url ?? m.container_image ?? '—'}
                  </TableCell>
                  <TableCell className="tabular">{m.resources.gpu_count}</TableCell>
                  <TableCell>
                    <Badge className="badge" variant={m.active ? 'outline' : 'muted'}>
                      {m.active ? '활성' : '비활성'}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge
                      className="badge"
                      variant={m.approval_status === 'approved' ? 'outline' : 'muted'}
                    >
                      {m.approval_status === 'approved'
                        ? '승인'
                        : m.approval_status === 'pending'
                          ? '대기'
                          : '반려'}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => toggle(m.model_id, m.version, m.active)}
                      >
                        {m.active ? '비활성' : '활성'}
                      </Button>
                      {m.approval_status !== 'approved' && (
                        <Button variant="outline" size="sm" onClick={() => approve(m.model_id, m.version)}>
                          승인
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty>등록된 모델이 없습니다.</Empty>
        )}
      </Panel>
    </>
  )
}
