# CORE Server
- User management / authenication

```
select a.employee_id,a.date_start,b.date_stop,a.date,(b.date_stop-a.date_start) as duration from timeclock_shifts as a join timeclock_shifts as b on a.employee_id=b.employee_id and a.date_stop=b.date_start;
```


```
select
	employee_id,
	date_start,
	date_stop,
	shift_num
from (
	select *,
		sum(case when nearby = true then 0 else 1 end) over (partition by employee_id order by num) as shift_num
	from (
		select *,
			row_number() over (partition by employee_id order by date_start asc) num,
			case when prev_stop is NULL then false else ((date_start - prev_stop) < interval '6 hour') end as nearby
		from (
			select *,
				lag(date_stop, 1) over (partition by employee_id order by date_start asc) prev_stop
			from timeclock_shifts
		) t
	) t
) t order by date_start desc;
```
