import { Skeleton as AntdSkeleton, Card } from 'antd';

/**
 * Shared loading skeletons. Replaces the bare `<Spin />` placeholders with
 * table/card-shaped skeletons that match the layout that will appear once
 * the data arrives — users see the right shape is coming, not a centered
 * spinner.
 *
 * Keep these thin: they only set defaults; consumers can pass any Skeleton
 * props to override.
 */

interface TableSkeletonProps {
  rows?: number;
  columns?: number;
}

export function TableSkeleton({ rows = 5, columns = 4 }: TableSkeletonProps) {
  return (
    <AntdSkeleton
      active
      paragraph={{
        rows,
        width: Array.from({ length: columns }, (_, i) => 60 + ((i * 17) % 35)),
      }}
    />
  );
}

interface CardSkeletonProps {
  rows?: number;
}

export function CardSkeleton({ rows = 3 }: CardSkeletonProps) {
  return (
    <Card>
      <AntdSkeleton active paragraph={{ rows }} />
    </Card>
  );
}

/** Spinner-style fallback for tight spaces (e.g. inline button area). */
export function InlineSkeleton() {
  return <AntdSkeleton.Input active size="small" style={{ width: 120 }} />;
}